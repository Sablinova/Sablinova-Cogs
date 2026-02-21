# SPDX-FileCopyrightText: 2025 cswimr <copyright@csw.im>
# SPDX-License-Identifier: MPL-2.0

import contextlib
import json
import re

from redbot.cogs.downloader import errors
from redbot.cogs.downloader.converters import InstalledCog
from redbot.core import commands
from redbot.core.utils import chat_formatting as cf
# from tidegear import Cog
# from tidegear.utils import send_error


# Disable Ruff complaining about accessing private members
# That's kind of necessary for this cog to function because the Downloader cog has a limited public API
# ruff: noqa: SLF001 # Private member access
class Backup(commands.Cog):
    """A utility to make reinstalling repositories and cogs after migrating the bot far easier."""

    def __init__(self, bot):
        self.bot = bot

    async def send_error(self, ctx, content):
        """Internal helper to replace tidegear.utils.send_error"""
        await ctx.send(f"❌ **Error:** {content}")

    @commands.group(autohelp=True)  # pyright: ignore[reportArgumentType]
    @commands.is_owner()
    async def backup(self, ctx: commands.Context) -> None:
        """Backup your installed cogs."""

    @backup.command(name="export")
    @commands.is_owner()
    @commands.bot_has_permissions(attach_files=True)
    async def backup_export(self, ctx: commands.Context) -> None:
        """Export your installed repositories and cogs to a file."""
        downloader = ctx.bot.get_cog("Downloader")
        if downloader is None:
            await self.send_error(
                ctx, content=f"You do not have the `Downloader` cog loaded. Please run `{ctx.clean_prefix}load downloader` and try again."
            )
            return

        all_repos = list(downloader._repo_manager.repos)

        export_data = []

        for repo in all_repos:
            repo_dict = {
                "name": repo.name,
                "url": repo.url,
                "branch": repo.branch,
                "cogs": [],
            }

            cogs = await downloader.installed_cogs()

            for cog in cogs:
                if cog.repo_name == repo.name:
                    cog_dict = {
                        "name": cog.name,
                        # "loaded": cog.name in ctx.bot.extensions.keys(),  # noqa: ERA001
                        # this functionality was planned but never implemented due to Red limitations
                        # and the possibility of restoration functionality being added to Core
                        "pinned": cog.pinned,
                        "commit": cog.commit,
                    }
                    repo_dict["cogs"].append(cog_dict)

            export_data.append(repo_dict)

        await ctx.send(file=cf.text_to_file(json.dumps(export_data, indent=4), "backup.json"))

    @backup.command(name="import")
    @commands.is_owner()
    @commands.bot_has_permissions(attach_files=True)
    async def backup_import(self, ctx: commands.Context) -> None:
        """Import your installed repositories and cogs from an export file."""
        try:
            export = json.loads(await ctx.message.attachments[0].read())
        except (json.JSONDecodeError, IndexError):
            try:
                export = json.loads(await ctx.message.reference.resolved.attachments[0].read())  # type: ignore - this is fine to let error because it gets handled
            except (json.JSONDecodeError, IndexError, AttributeError):
                await self.send_error(ctx, content="Please provide a valid JSON export file.")
                return

        downloader = ctx.bot.get_cog("Downloader")
        if downloader is None:
            await self.send_error(
                ctx, content=f"You do not have the `Downloader` cog loaded. Please run `{ctx.clean_prefix}load downloader` and try again."
            )
            return

        repo_s = []
        uninstall_s = []
        install_s = []
        repo_e = []
        uninstall_e = []
        install_e = []

        async with ctx.typing():
            for repo in export:
                # Most of this code is from the Downloader cog.
                name = repo["name"]
                branch = repo["branch"]
                url = repo["url"]
                cogs = repo["cogs"]

                if "PyLav/Red-Cogs" in url:
                    repo_e.append("PyLav cogs are not supported.")
                    continue
                if name.startswith(".") or name.endswith("."):
                    repo_e.append(f"Invalid repository name: {name}\nRepository names cannot start or end with a dot.")
                    continue
                if re.match(r"^[a-zA-Z0-9_\-\.]+$", name) is None:
                    repo_e.append(
                        f"Invalid repository name: {name}\nRepository names may only contain "
                        "letters, numbers, underscores, hyphens, and dots."
                    )
                    continue

                try:
                    repository = await downloader._repo_manager.add_repo(url, name, branch)
                    repo_s.append(f"Added repository {name} from {url} on branch {branch}.")
                    self.logger.debug("Added repository %s from %s on branch %s", name, url, branch)

                except errors.ExistingGitRepo:
                    repo_e.append(f"Repository {name} already exists.")
                    repository = downloader._repo_manager.get_repo(name)
                    self.logger.debug("Repository %s already exists", name)

                except errors.AuthenticationError as err:
                    repo_e.append(f"Authentication error while adding repository {name}. See logs for more information.")
                    self.logger.exception(
                        "Something went wrong whilst cloning %s (to revision %s)",
                        url,
                        branch,
                        exc_info=err,
                    )
                    continue

                except errors.CloningError as err:
                    repo_e.append(f"Cloning error while adding repository {name}. See logs for more information.")
                    self.logger.exception(
                        "Something went wrong whilst cloning %s (to revision %s)",
                        url,
                        branch,
                        exc_info=err,
                    )
                    continue

                except OSError:
                    repo_e.append(f"OS error while adding repository {name}. See logs for more information.")
                    self.logger.exception(
                        "Something went wrong trying to add repo %s under name %s",
                        url,
                        name,
                    )
                    continue

                cog_modules = []
                for cog in cogs:
                    # Prevent self-restore loop
                    if cog["name"] == "backup" and ("cswimr/SeaCogs" in url or "Sablinova/Sablinova-Cogs" in url):
                        continue
                    try:
                        cog_module = await InstalledCog.convert(ctx, cog["name"])
                    except commands.BadArgument:
                        uninstall_e.append(f"Failed to uninstall {cog['name']}")
                        continue
                    cog_modules.append(cog_module)

                for cog in {cog.name for cog in cog_modules}:
                    poss_installed_path = (await downloader.cog_install_path()) / cog
                    if poss_installed_path.exists():
                        with contextlib.suppress(commands.ExtensionNotLoaded):
                            await ctx.bot.unload_extension(cog)
                            await ctx.bot.remove_loaded_package(cog)
                        await downloader._delete_cog(poss_installed_path)
                        uninstall_s.append(f"Uninstalled {cog}")
                        self.logger.debug("Uninstalled %s", cog)
                    else:
                        uninstall_e.append(f"Failed to uninstall {cog}")
                        self.logger.warning("Failed to uninstall %s", cog)
                await downloader._remove_from_installed(cog_modules)

                for cog in cogs:
                    cog_name = cog["name"]
                    cog_pinned = cog["pinned"]
                    if cog_pinned:
                        commit = cog["commit"]
                    else:
                        commit = None

                    if cog_name == "backup" and ("cswimr/SeaCogs" in url.lower() or "Sablinova/Sablinova-Cogs" in url):
                        continue

                    async with repository.checkout(commit, exit_to_rev=repository.branch):
                        cogs_c, message = await downloader._filter_incorrect_cogs_by_names(repository, [cog_name])
                        if not cogs_c:
                            install_e.append(message)
                            self.logger.error(message)
                            continue
                        failed_reqs = await downloader._install_requirements(cogs_c)
                        if failed_reqs:
                            install_e.append(f"Failed to install {cog_name} due to missing requirements: {failed_reqs}")
                            self.logger.error(
                                "Failed to install %s due to missing requirements: %s",
                                cog_name,
                                failed_reqs,
                            )
                            continue

                        installed_cogs, failed_cogs = await downloader._install_cogs(cogs_c)

                        if repository.available_libraries:
                            installed_libs, failed_libs = await repository.install_libraries(
                                target_dir=downloader.SHAREDLIB_PATH,
                                req_target_dir=downloader.LIB_PATH,
                            )
                        else:
                            installed_libs = None
                            failed_libs = None

                        if cog_pinned:
                            for cog in installed_cogs:  # noqa: PLW2901
                                cog.pinned = True

                        await downloader._save_to_installed(installed_cogs + installed_libs if installed_libs else installed_cogs)
                        if installed_cogs:
                            installed_cog_name = installed_cogs[0].name
                            install_s.append(f"Installed {installed_cog_name}")
                            self.logger.debug("Installed %s", installed_cog_name)
                        if installed_libs:
                            for lib in installed_libs:
                                install_s.append(f"Installed {lib.name} required for {cog_name}")
                                self.logger.debug("Installed %s required for %s", lib.name, cog_name)
                        if failed_cogs:
                            failed_cog_name = failed_cogs[0].name
                            install_e.append(f"Failed to install {failed_cog_name}")
                            self.logger.error("Failed to install %s", failed_cog_name)
                        if failed_libs:
                            for lib in failed_libs:
                                install_e.append(f"Failed to install {lib.name} required for {cog_name}")
                                self.logger.error(
                                    "Failed to install %s required for %s",
                                    lib.name,
                                    cog_name,
                                )
        await ctx.send(
            "Import complete!",
            file=cf.text_to_file(
                (
                    f"Repositories:\n{repo_s}\n\nRepository Errors:\n{repo_e}\n\nUninstalled Cogs:\n{uninstall_s}\n"
                    f"\nUninstalled Cogs Errors:\n{uninstall_e}\n\nInstalled Cogs:\n{install_s}\n\nInstalled Cogs Errors:\n{install_e}"
                ),
                "backup.log",
            ),
        )
