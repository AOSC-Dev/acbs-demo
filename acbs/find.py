from typing import List, Optional, Dict, Tuple, Deque
from collections import OrderedDict, defaultdict, deque
from acbs.parser import parse_package, ACBSPackageInfo
import os


def find_package(name: str, search_path: str, group=False) -> List[ACBSPackageInfo]:
    with os.scandir(search_path) as it:
        # scan categories
        for entry in it:
            if not entry.is_dir():
                continue
            with os.scandir(os.path.join(search_path, entry.name)) as inner:
                # scan package directories
                for entry_inner in inner:
                    if not entry_inner.is_dir():
                        continue
                    full_search_path = os.path.join(
                        search_path, entry.name, entry_inner.name, 'autobuild')
                    if entry_inner.name == name and os.path.isdir(full_search_path):
                        return [parse_package(full_search_path)]
                    if not group:
                        continue
                    # is this a package group?
                    with os.scandir(os.path.join(search_path, entry.name, entry_inner.name)) as group:
                        # scan potential package groups
                        for entry_group in group:
                            full_search_path = os.path.join(
                                search_path, entry.name, entry_inner.name, entry_group.name)
                            # condition: `defines` inside a folder but not named `autobuild`
                            if os.path.basename(full_search_path) == 'autobuild' or not os.path.isfile(os.path.join(full_search_path, 'defines')):
                                continue
                            # because the package inside the group will have a different name than the folder name
                            # we will parse the defines file to decide
                            result = parse_package(full_search_path)
                            if result and result.name == name:
                                # name of the package inside the group
                                package_alias = os.path.basename(
                                    full_search_path)
                                try:
                                    group_seq = int(
                                        package_alias.split('-')[0])
                                except (ValueError, IndexError) as ex:
                                    raise ValueError('Invalid package alias: {alias}'.format(
                                        alias=package_alias)) from ex
                                group_root = os.path.realpath(
                                    os.path.join(full_search_path, '..'))
                                group_category = os.path.realpath(
                                    os.path.join(group_root, '..'))
                                result.base_slug = '{cat}/{root}'.format(cat=os.path.basename(
                                    group_category), root=os.path.basename(group_root))
                                result.group_seq = group_seq
                                group_result = expand_package_group(result, search_path)
                                return group_result
    if group:
        return []
    else:
        # if cannot find a package without considering it as part of a group
        # then re-search with group enabled
        return find_package(name, search_path, True)


def hoist_package_groups(packages: List[List[ACBSPackageInfo]]):
    """In AOSC OS build rules, the package group need to be built sequentially together.
    This function will check if the package inside the group will be built sequentially
    """
    groups_seen = set()
    for group in packages:
        for pkg in group:
            base_slug = pkg.base_slug
            if not base_slug:
                continue
            if base_slug in groups_seen:
                group.remove(pkg)
            else:
                pkg.group_seq = 0
                groups_seen.add(base_slug)


def expand_package_group(package: ACBSPackageInfo, search_path: str) -> List[ACBSPackageInfo]:
    group_root = os.path.join(search_path, package.base_slug)
    actionables: List[ACBSPackageInfo] = []
    for entry in os.scandir(group_root):
        if not entry.is_dir():
            continue
        name = entry.name
        splitted = name.split('-', 1)
        if len(splitted) != 2:
            raise ValueError(
                'Malformed sub-package name: {name}'.format(name=entry.name))
        try:
            sequence = int(splitted[0])
            package = parse_package(entry.path)
            if package:
                package.group_seq = sequence
                actionables.append(package)
        except ValueError as ex:
            raise ValueError(
                'Malformed sub-package name: {name}'.format(name=entry.name)) from ex
    # because the directory order is arbitrary, we need to sort them
    sorted(actionables, key=lambda a: a.group_seq)
    return actionables
