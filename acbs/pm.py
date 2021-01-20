import logging
import re
import subprocess
from typing import List, Dict

from acbs.base import ACBSPackageInfo

installed_cache: Dict[str, bool] = {}
available_cache: Dict[str, bool] = {}
use_native_bindings: bool = True

try:
    from acbs.miniapt_query import apt_init_system, check_if_available as apt_check_if_available
    if not apt_init_system():
        raise ImportError('Initialization failure.')
except ImportError:
    use_native_bindings = False


def filter_dependencies(package: ACBSPackageInfo) -> ACBSPackageInfo:
    installables = []
    deps = []
    for dep in package.deps:
        if check_if_installed(dep):
            continue
        if check_if_available(dep):
            installables.append(dep)
            continue
        deps.append(dep)
    package.deps = deps
    package.installables = installables
    return package


def escape_package_name(name: str) -> str:
    return re.sub(r'([+*?])', '\\\\\\1', name)


def escape_package_name_install(name: str) -> str:
    escaped = escape_package_name(name)
    if escaped.endswith('+') or escaped.endswith('-'):
        return '{}+'.format(escaped)
    return escaped


def fix_pm_states(escaped: List[str]):
    count = 0
    while count < 3:
        try:
            subprocess.check_call(['apt-get', 'install', '-yf'])
            command = ['apt-get', 'install', '-y']
            command.extend(escaped)
            subprocess.check_call(command)
            return
        except subprocess.CalledProcessError:
            count += 1
            continue
    raise RuntimeError('Unable to correct package manager states...')


def check_if_installed(name: str) -> bool:
    logging.debug('Checking if %s is installed' % name)
    cached = installed_cache.get(name)
    if cached is not None:
        return cached
    try:
        subprocess.check_output(['dpkg', '-s', name], stderr=subprocess.STDOUT)
        installed_cache[name] = True
        return True
    except subprocess.CalledProcessError:
        installed_cache[name] = False
        return False


def check_if_available(name: str) -> bool:
    logging.debug('Checking if %s is available' % name)
    cached = available_cache.get(name)
    if cached is not None:
        return cached
    if use_native_bindings:
        logging.debug('... using libapt-pkg')
        if apt_check_if_available(name) != 1:
            return False
    try:
        subprocess.check_output(
            ['apt-cache', 'show', escape_package_name(name)], stderr=subprocess.STDOUT)
        logging.debug('Checking if %s can be installed' % name)
        subprocess.check_output(
            ['apt-get', 'install', '-s', name], stderr=subprocess.STDOUT)
        available_cache[name] = True
        return True
    except subprocess.CalledProcessError:
        available_cache[name] = False
        return False


def install_from_repo(packages: List[str]):
    logging.debug('Installing %s' % packages)
    escaped = []
    for package in packages:
        escaped.append(escape_package_name_install(package))
    command = ['apt-get', 'install', '-y']
    command.extend(escaped)
    try:
        subprocess.check_call(command)
    except subprocess.CalledProcessError:
        logging.warning(
            'Failed to install dependencies, attempting to correct issues...')
        fix_pm_states(escaped)
    return
