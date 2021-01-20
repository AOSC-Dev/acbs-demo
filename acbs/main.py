import logging
import logging.handlers
import os
import sys
import time
import traceback
from pathlib import Path
from typing import List, Tuple

import acbs.fetch
import acbs.parser

from acbs import __version__
from acbs.checkpoint import ACBSShrinkWrap, do_shrink_wrap
from acbs.const import CONF_DIR, DUMP_DIR, LOG_DIR, TMP_DIR
from acbs.deps import tarjan_search
from acbs.fetch import fetch_source, process_source
from acbs.find import check_package_groups, find_package
from acbs.parser import get_deps_graph, get_tree_by_name
from acbs.pm import install_from_repo
from acbs.utils import (ACBSLogFormatter, full_line_banner, guess_subdir,
                        has_stamp, invoke_autobuild, make_build_dir,
                        print_build_timings, print_package_names, write_checksums,
                        generate_checksums, is_spec_legacy)


class BuildCore(object):

    def __init__(self, args) -> None:
        self.debug = args.debug
        self.no_deps = args.no_deps
        self.dl_only = args.get
        self.tree = 'default'
        self.build_queue = args.packages
        self.generate = args.acbs_write
        self.tree_dir = ''
        self.package_cursor = 0
        # static vars
        self.conf_dir = CONF_DIR
        self.dump_dir = DUMP_DIR
        self.tmp_dir = TMP_DIR
        self.log_dir = LOG_DIR

        if args.acbs_tree:
            self.tree = args.acbs_tree[0]
        self.init()

    def init(self) -> None:
        sys.excepthook = self.acbs_except_hdr
        print(full_line_banner(
            'Welcome to ACBS - {}'.format(__version__)))
        if self.debug:
            log_verbosity = logging.DEBUG
        else:
            log_verbosity = logging.INFO
        try:
            for directory in [self.dump_dir, self.tmp_dir, self.conf_dir,
                              self.log_dir]:
                if not os.path.isdir(directory):
                    os.makedirs(directory)
        except Exception:
            raise IOError('\033[93mFailed to create work directories\033[0m!')
        self.__install_logger(log_verbosity)
        forest_file = os.path.join(self.conf_dir, 'forest.conf')
        if os.path.exists(forest_file):
            self.tree_dir = get_tree_by_name(forest_file, self.tree)
            if not self.tree_dir:
                raise ValueError('Tree not found!')
        else:
            raise Exception('forest.conf not found')

    def __install_logger(self, str_verbosity=logging.INFO,
                         file_verbosity=logging.DEBUG):
        logger = logging.getLogger()
        logger.setLevel(0)  # Set to lowest to bypass the initial filter
        str_handler = logging.StreamHandler()
        str_handler.setLevel(str_verbosity)
        str_handler.setFormatter(ACBSLogFormatter())
        logger.addHandler(str_handler)
        log_file_handler = logging.handlers.RotatingFileHandler(
            os.path.join(self.log_dir, 'acbs-build.log'), mode='a', maxBytes=2e5, backupCount=3)
        log_file_handler.setLevel(file_verbosity)
        log_file_handler.setFormatter(logging.Formatter(
            '%(asctime)s:%(levelname)s:%(message)s'))
        logger.addHandler(log_file_handler)

    def build(self) -> None:
        packages = []
        build_timings: List[Tuple[str, float]] = []
        acbs.fetch.generate_mode = self.generate
        acbs.parser.generate_mode = self.generate
        # begin finding and resolving dependencies
        logging.info('Searching and resolving dependencies...')
        for i in self.build_queue:
            logging.debug('Finding {}...'.format(i))
            package = find_package(i, self.tree_dir)
            if not package:
                raise RuntimeError('Could not find package {}'.format(i))
            packages.extend(package)
        resolved = self.resolve_deps(packages)
        logging.info(
            'Dependencies resolved, {} packages in the queue'.format(len(resolved)))
        logging.debug('Queue: {}'.format(packages))
        logging.info('Packages to be built: {}'.format(
            print_package_names(packages, 5)))
        try:
            self.build_sequential(build_timings, packages)
        except Exception as ex:
            logging.exception(ex)
            self.save_checkpoint(build_timings, packages)
        print_build_timings(build_timings)

    def save_checkpoint(self, build_timings, packages):
        logging.info('ACBS is trying to save your build status...')
        shrink_wrap = ACBSShrinkWrap(
            self.package_cursor, build_timings, packages, self.no_deps)
        filename = do_shrink_wrap(shrink_wrap, '/tmp')
        logging.info('... saved to {}'.format(filename))
        raise RuntimeError(
            'Build error.\nUse `acbs-build --resume {}` to resume after you sorted out the situation.'.format(filename))

    def resolve_deps(self, packages):
        error = False
        if not self.no_deps:
            logging.debug('Converting queue into adjacency graph...')
            graph = get_deps_graph(packages)
            logging.debug('Running Tarjan search...')
            resolved = tarjan_search(graph, self.tree_dir)
        else:
            logging.warning('Warning: Dependency resolution disabled!')
            resolved = [[package] for package in packages]
        # print a newline
        print()
        packages.clear()  # clear package list for the search results
        # here we will check if there is any loop in the dependency graph
        for dep in resolved:
            if len(dep) > 1 or dep[0].name in dep[0].deps:
                # this is a SCC, aka a loop
                logging.error('Found a loop in the dependency graph: {}'.format(
                    print_package_names(dep)))
                error = True
            elif not error:
                packages.extend(dep)
        if error:
            raise RuntimeError(
                'Dependencies NOT resolved. Couldn\'t continue!')
        check_package_groups(packages)
        return resolved

    def build_sequential(self, build_timings, packages):
        # build process
        for task in packages:
            self.package_cursor += 1
            logging.info('Building {} ({}/{})...'.format(task.name,
                                                         self.package_cursor, len(packages)))
            source_name = task.name
            if task.base_slug:
                source_name = os.path.basename(task.base_slug)
            if not has_stamp(task.build_location):
                fetch_source(task.source_uri, self.dump_dir, source_name)
            if self.dl_only:
                if self.generate:
                    spec_location = os.path.join(
                        task.script_location, '..', 'spec')
                    is_legacy = is_spec_legacy(spec_location)
                    checksum = generate_checksums(task.source_uri, is_legacy)
                    write_checksums(spec_location, checksum)
                    logging.info('Updated checksum for {}'.format(task.name))
                build_timings.append((task.name, -1))
                continue
            if not task.build_location:
                build_dir = make_build_dir(self.tmp_dir)
                task.build_location = build_dir
                process_source(task, source_name)
            else:
                # First sub-package in a meta-package
                if not has_stamp(task.build_location):
                    process_source(task, source_name)
                    Path(os.path.join(task.build_location, '.acbs-stamp')).touch()
                build_dir = task.build_location
            if task.subdir:
                build_dir = os.path.join(build_dir, task.subdir)
            else:
                subdir = guess_subdir(build_dir)
                if not subdir:
                    raise RuntimeError(
                        'Could not determine sub-directory, please specify manually.')
                build_dir = os.path.join(build_dir, subdir)
            if task.installables:
                logging.info('Installing dependencies from repository...')
                install_from_repo(task.installables)
            start = time.monotonic()
            try:
                invoke_autobuild(task, build_dir)
            except Exception:
                # early printing of build summary before exploding
                if build_timings:
                    print_build_timings(build_timings)
                raise RuntimeError(
                    'Error when building {}.\nBuild folder: {}'.format(task.name, build_dir))
            task_name = '{} ({} @ {}-{})'.format(task.name,
                                                 task.bin_arch, task.version, task.rel)
            build_timings.append((task_name, time.monotonic() - start))

    def acbs_except_hdr(self, type_, value, tb):
        logging.debug('Traceback:\n' + ''.join(traceback.format_tb(tb)))
        if self.debug:
            sys.__excepthook__(type_, value, tb)
        else:
            print()
            logging.fatal('Oops! \033[93m%s\033[0m: \033[93m%s\033[0m' % (
                str(type_.__name__), str(value)))
