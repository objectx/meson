# Copyright 2013-2014 The Meson development team

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# This file contains the detection logic for external
# dependencies. Mostly just uses pkg-config but also contains
# custom logic for packages that don't provide them.

# Currently one file, should probably be split into a
# package before this gets too big.

import os, stat, glob, subprocess, shutil
from coredata import MesonException
import environment
import mlog
import mesonlib

class DependencyException(MesonException):
    def __init__(self, *args, **kwargs):
        MesonException.__init__(self, *args, **kwargs)

class CustomRule:
    def __init__(self, cmd_list, name_templ, src_keyword, name, description):
        self.cmd_list = cmd_list
        self.name_templ = name_templ
        self.src_keyword = src_keyword
        self.name = name
        self.description = description

class Dependency():
    def __init__(self):
        self.name = "null"

    def get_compile_args(self):
        return []

    def get_link_args(self):
        return []

    def found(self):
        return False

    def get_sources(self):
        """Source files that need to be added to the target.
        As an example, gtest-all.cc when using GTest."""
        return []

    def get_name(self):
        return self.name

    # Rules for commands to execute before compilation
    # such as Qt's moc preprocessor.
    def get_generate_rules(self):
        return []

    def get_exe_args(self):
        return []

    def need_threads(self):
        return False

class PkgConfigDependency(Dependency):
    pkgconfig_found = None

    def __init__(self, name, kwargs):
        required = kwargs.get('required', True)
        Dependency.__init__(self)
        self.name = name
        if PkgConfigDependency.pkgconfig_found is None:
            self.check_pkgconfig()

        if not PkgConfigDependency.pkgconfig_found:
            raise DependencyException('Pkg-config not found.')
        self.is_found = False
        p = subprocess.Popen(['pkg-config', '--modversion', name], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE)
        out = p.communicate()[0]
        if p.returncode != 0:
            mlog.log('Dependency', name, 'found:', mlog.red('NO'))
            if required:
                raise DependencyException('Required dependency %s not found.' % name)
            self.modversion = 'none'
            self.cargs = []
            self.libs = []
        else:
            self.modversion = out.decode().strip()
            mlog.log('Dependency', mlog.bold(name), 'found:', mlog.green('YES'), self.modversion)
            version_requirement = kwargs.get('version', None)
            if version_requirement is None:
                self.is_found = True
            else:
                if not isinstance(version_requirement, str):
                    raise DependencyException('Version argument must be string.')
                self.is_found = mesonlib.version_compare(self.modversion, version_requirement)
                if not self.is_found and required:
                    raise DependencyException('Invalid version of a dependency, needed %s %s found %s.' % (name, version_requirement, self.modversion))
            if not self.is_found:
                return
            p = subprocess.Popen(['pkg-config', '--cflags', name], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE)
            out = p.communicate()[0]
            if p.returncode != 0:
                raise RuntimeError('Could not generate cargs for %s.' % name)
            self.cargs = out.decode().split()

            p = subprocess.Popen(['pkg-config', '--libs', name], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE)
            out = p.communicate()[0]
            if p.returncode != 0:
                raise RuntimeError('Could not generate libs for %s.' % name)
            self.libs = out.decode().split()

    def get_modversion(self):
        return self.modversion

    def get_compile_args(self):
        return self.cargs

    def get_link_args(self):
        return self.libs

    def check_pkgconfig(self):
        try:
            p = subprocess.Popen(['pkg-config', '--version'], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE)
            out = p.communicate()[0]
            if p.returncode == 0:
                mlog.log('Found pkg-config:', mlog.bold(shutil.which('pkg-config')),
                         '(%s)' % out.decode().strip())
                PkgConfigDependency.pkgconfig_found = True
                return
        except Exception:
            pass
        PkgConfigDependency.pkgconfig_found = False
        mlog.log('Found Pkg-config:', mlog.red('NO'))

    def found(self):
        return self.is_found

class WxDependency(Dependency):
    wx_found = None

    def __init__(self, kwargs):
        Dependency.__init__(self)
        if WxDependency.wx_found is None:
            self.check_wxconfig()

        if not WxDependency.wx_found:
            raise DependencyException('Wx-config not found.')
        self.is_found = False
        p = subprocess.Popen([self.wxc, '--version'], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE)
        out = p.communicate()[0]
        if p.returncode != 0:
            mlog.log('Dependency wxwidgets found:', mlog.red('NO'))
            self.cargs = []
            self.libs = []
        else:
            self.modversion = out.decode().strip()
            version_req = kwargs.get('version', None)
            if version_req is not None:
                if not mesonlib.version_compare(self.modversion, version_req):
                    mlog.log('Wxwidgets version %s does not fullfill requirement %s' %\
                             (self.modversion, version_req))
                    return
            mlog.log('Dependency wxwidgets found:', mlog.green('YES'))
            self.is_found = True
            # wx-config seems to have a cflags as well but since it requires C++,
            # this should be good, at least for now.
            p = subprocess.Popen([self.wxc, '--cxxflags'], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE)
            out = p.communicate()[0]
            if p.returncode != 0:
                raise RuntimeError('Could not generate cargs for wxwidgets.')
            self.cargs = out.decode().split()

            p = subprocess.Popen([self.wxc, '--libs'], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE)
            out = p.communicate()[0]
            if p.returncode != 0:
                raise RuntimeError('Could not generate libs for wxwidgets.')
            self.libs = out.decode().split()

    def get_modversion(self):
        return self.modversion

    def get_compile_args(self):
        return self.cargs

    def get_link_args(self):
        return self.libs

    def check_wxconfig(self):
        for wxc in ['wx-config-3.0', 'wx-config']:
            try:
                p = subprocess.Popen([wxc, '--version'], stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE)
                out = p.communicate()[0]
                if p.returncode == 0:
                    mlog.log('Found wx-config:', mlog.bold(shutil.which(wxc)),
                             '(%s)' % out.decode().strip())
                    self.wxc = wxc
                    WxDependency.wx_found = True
                    return
            except Exception:
                pass
        WxDependency.wxconfig_found = False
        mlog.log('Found wx-config:', mlog.red('NO'))

    def found(self):
        return self.is_found

class ExternalProgram():
    def __init__(self, name, fullpath=None, silent=False, search_dir=None):
        self.name = name
        self.fullpath = None
        if fullpath is not None:
            if not isinstance(fullpath, list):
                self.fullpath = [fullpath]
            else:
                self.fullpath = fullpath
        else:
            self.fullpath = [shutil.which(name)]
            if self.fullpath[0] is None and search_dir is not None:
                trial = os.path.join(search_dir, name)
                suffix = os.path.splitext(trial)[-1].lower()[1:]
                if mesonlib.is_windows() and (suffix == 'exe' or suffix == 'com'\
                                          or suffix == 'bat'):
                    self.fullpath = [trial]
                elif not mesonlib.is_windows() and os.access(trial, os.X_OK):
                    self.fullpath = [trial]
                else:
                    # Now getting desperate. Maybe it is a script file that is a) not chmodded
                    # executable or b) we are on windows so they can't be directly executed.
                    try:
                        first_line = open(trial).readline().strip()
                        if first_line.startswith('#!'):
                            commands = first_line[2:].split('#')[0].strip().split()
                            if mesonlib.is_windows():
                                commands[0] = commands[0].split('/')[-1] # Windows does not have /usr/bin.
                            self.fullpath = commands + [trial]
                    except Exception:
                        pass
        if not silent:
            if self.found():
                mlog.log('Program', mlog.bold(name), 'found:', mlog.green('YES'), '(%s)' % ' '.join(self.fullpath))
            else:
                mlog.log('Program', mlog.bold(name), 'found:', mlog.red('NO'))

    def found(self):
        return self.fullpath[0] is not None

    def get_command(self):
        return self.fullpath

    def get_name(self):
        return self.name

class ExternalLibrary(Dependency):
    def __init__(self, name, fullpath=None, silent=False):
        super().__init__()
        self.name = name
        self.fullpath = fullpath
        if not silent:
            if self.found():
                mlog.log('Library', mlog.bold(name), 'found:', mlog.green('YES'), '(%s)' % self.fullpath)
            else:
                mlog.log('Library', mlog.bold(name), 'found:', mlog.red('NO'))

    def found(self):
        return self.fullpath is not None

    def get_link_args(self):
        if self.found():
            return [self.fullpath]
        return []

class BoostDependency(Dependency):
    # Some boost libraries have different names for
    # their sources and libraries. This dict maps
    # between the two.
    name2lib = {'test' : 'unit_test_framework'}

    def __init__(self, kwargs):
        Dependency.__init__(self)
        self.name = 'boost'
        try:
            self.boost_root = os.environ['BOOST_ROOT']
            if not os.path.isabs(self.boost_root):
                raise DependencyException('BOOST_ROOT must be an absolute path.')
        except KeyError:
            self.boost_root = None
        if self.boost_root is None:
            self.incdir = '/usr/include/boost'
        else:
            self.incdir = os.path.join(self.boost_root, 'include/boost')
        self.src_modules = {}
        self.lib_modules = {}
        self.lib_modules_mt = {}
        self.detect_version()
        self.requested_modules = self.get_requested(kwargs)
        module_str = ', '.join(self.requested_modules)
        if self.version is not None:
            self.detect_src_modules()
            self.detect_lib_modules()
            self.validate_requested()
            if self.boost_root is not None:
                info = self.version + ', ' + self.boost_root
            else:
                info = self.version
            mlog.log('Dependency Boost (%s) found:' % module_str, mlog.green('YES'),
                     '(' + info + ')')
        else:
            mlog.log("Dependency Boost (%s) found:" % module_str, mlog.red('NO'))

    def get_compile_args(self):
        args = []
        if self.boost_root is not None:
            args.append('-I' + os.path.join(self.boost_root, 'include'))
        return args

    def get_requested(self, kwargs):
        modules = 'modules'
        if not modules in kwargs:
            raise DependencyException('Boost dependency must specify "%s" keyword.' % modules)
        candidates = kwargs[modules]
        if isinstance(candidates, str):
            return [candidates]
        for c in candidates:
            if not isinstance(c, str):
                raise DependencyException('Boost module argument is not a string.')
        return candidates

    def validate_requested(self):
        for m in self.requested_modules:
            if m not in self.src_modules:
                raise DependencyException('Requested Boost module "%s" not found.' % m)

    def found(self):
        return self.version is not None

    def get_version(self):
        return self.version

    def detect_version(self):
        try:
            ifile = open(os.path.join(self.incdir, 'version.hpp'))
        except FileNotFoundError:
            self.version = None
            return
        for line in ifile:
            if line.startswith("#define") and 'BOOST_LIB_VERSION' in line:
                ver = line.split()[-1]
                ver = ver[1:-1]
                self.version = ver.replace('_', '.')
                return
        self.version = None

    def detect_src_modules(self):
        for entry in os.listdir(self.incdir):
            entry = os.path.join(self.incdir, entry)
            if stat.S_ISDIR(os.stat(entry).st_mode):
                self.src_modules[os.path.split(entry)[-1]] = True

    def detect_lib_modules(self):
        globber = 'libboost_*.so' # FIXME, make platform independent.
        if self.boost_root is None:
            libdirs = mesonlib.get_library_dirs()
        else:
            libdirs = [os.path.join(self.boost_root, 'lib')]
        for libdir in libdirs:
            for entry in glob.glob(os.path.join(libdir, globber)):
                lib = os.path.basename(entry)
                name = lib.split('.')[0].split('_', 1)[-1]
                # I'm not 100% sure what to do here. Some distros
                # have modules such as thread only as -mt versions.
                if entry.endswith('-mt.so'):
                    self.lib_modules_mt[name] = True
                else:
                    self.lib_modules[name] = True

    def get_link_args(self):
        args = []
        if self.boost_root:
            # FIXME, these are in gcc format, not msvc.
            # On the other hand, so are the args that
            # pkg-config returns.
            args.append('-L' + os.path.join(self.boost_root, 'lib'))
        for module in self.requested_modules:
            module = BoostDependency.name2lib.get(module, module)
            if module in self.lib_modules or module in self.lib_modules_mt:
                linkcmd = '-lboost_' + module
                args.append(linkcmd)
                # FIXME a hack, but Boost's testing framework has a lot of
                # different options and it's hard to determine what to do
                # without feedback from actual users. Update this
                # as we get more bug reports.
                if module == 'unit_testing_framework':
                    args.append('-lboost_test_exec_monitor')
            elif module + '-mt' in self.lib_modules_mt:
                linkcmd = '-lboost_' + module + '-mt'
                args.append(linkcmd)
                if module == 'unit_testing_framework':
                    args.append('-lboost_test_exec_monitor-mt')
        return args

    def get_sources(self):
        return []

    def need_threads(self):
        return 'thread' in self.requested_modules

class GTestDependency(Dependency):
    def __init__(self, kwargs):
        Dependency.__init__(self)
        self.main = kwargs.get('main', False)
        self.name = 'gtest'
        self.libname = 'libgtest.so'
        self.libmain_name = 'libgtest_main.so'
        self.include_dir = '/usr/include'
        self.src_include_dir = '/usr/src/gtest'
        self.src_dir = '/usr/src/gtest/src'
        self.all_src = os.path.join(self.src_dir, 'gtest-all.cc')
        self.main_src = os.path.join(self.src_dir, 'gtest_main.cc')
        self.detect()

    def found(self):
        return self.is_found

    def detect(self):
        trial_dirs = mesonlib.get_library_dirs()
        glib_found = False
        gmain_found = False
        for d in trial_dirs:
            if os.path.isfile(os.path.join(d, self.libname)):
                glib_found = True
            if os.path.isfile(os.path.join(d, self.libmain_name)):
                gmain_found = True
        if glib_found and gmain_found:
            self.is_found = True
            self.compile_args = []
            self.link_args = ['-lgtest']
            if self.main:
                self.link_args.append('-lgtest_main')
            self.sources = []
            mlog.log('Dependency GTest found:', mlog.green('YES'), '(prebuilt)')
        elif os.path.exists(self.src_dir):
            self.is_found = True
            self.compile_args = ['-I' + self.src_include_dir]
            self.link_args = []
            if self.main:
                self.sources = [self.all_src, self.main_src]
            else:
                self.sources = [self.all_src]
            mlog.log('Dependency GTest found:', mlog.green('YES'), '(building self)')
        else:
            mlog.log('Dependency GTest found:', mlog.red('NO'))
            self.is_found = False
        return self.is_found

    def get_compile_args(self):
        arr = []
        if self.include_dir != '/usr/include':
            arr.append('-I' + self.include_dir)
        arr.append('-I' + self.src_include_dir)
        return arr

    def get_link_args(self):
        return self.link_args
    def get_version(self):
        return '1.something_maybe'
    def get_sources(self):
        return self.sources

    def need_threads(self):
        return True

class GMockDependency(Dependency):
    def __init__(self, kwargs):
        Dependency.__init__(self)
        # GMock may be a library or just source.
        # Work with both.
        self.name = 'gmock'
        self.libname = 'libgmock.so'
        trial_dirs = mesonlib.get_library_dirs()
        gmock_found = False
        for d in trial_dirs:
            if os.path.isfile(os.path.join(d, self.libname)):
                gmock_found = True
        if gmock_found:
            self.is_found = True
            self.compile_args = []
            self.link_args = ['-lgmock']
            self.sources = []
            mlog.log('Dependency GMock found:', mlog.green('YES'), '(prebuilt)')
            return

        for d in ['/usr/src/gmock/src', '/usr/src/gmock']:
            if os.path.exists(d):
                self.is_found = True
                # Yes, we need both because there are multiple
                # versions of gmock that do different things.
                self.compile_args = ['-I/usr/src/gmock', '-I/usr/src/gmock/src']
                self.link_args = []
                all_src = os.path.join(d, 'gmock-all.cc')
                main_src = os.path.join(d, 'gmock_main.cc')
                if kwargs.get('main', False):
                    self.sources = [all_src, main_src]
                else:
                    self.sources = [all_src]
                mlog.log('Dependency GMock found:', mlog.green('YES'), '(building self)')
                return

        mlog.log('Dependency GMock found:', mlog.red('NO'))
        self.is_found = False

    def get_version(self):
        return '1.something_maybe'

    def get_compile_args(self):
        return self.compile_args

    def get_sources(self):
        return self.sources

    def get_link_args(self):
        return self.link_args

    def found(self):
        return self.is_found

qt5toolinfo_printed = False

class Qt5Dependency(Dependency):
    def __init__(self, kwargs):
        Dependency.__init__(self)
        self.name = 'qt5'
        self.root = '/usr'
        self.modules = []
        mods = kwargs.get('modules', [])
        if isinstance(mods, str):
            mods = [mods]
        for module in mods:
            self.modules.append(PkgConfigDependency('Qt5' + module, kwargs))
        if len(self.modules) == 0:
            raise DependencyException('No Qt5 modules specified.')
        if not qt5toolinfo_printed:
            mlog.log('Dependency Qt5 tools:')
        self.find_exes()

    def find_exes(self):
        # The binaries have different names on different
        # distros. Joy.
        global qt5toolinfo_printed
        self.moc = ExternalProgram('moc', silent=True)
        if not self.moc.found():
            self.moc = ExternalProgram('moc-qt5', silent=True)
        self.uic = ExternalProgram('uic', silent=True)
        if not self.uic.found():
            self.uic = ExternalProgram('uic-qt5', silent=True)
        self.rcc = ExternalProgram('rcc', silent=True)
        if not self.rcc.found():
            self.rcc = ExternalProgram('rcc-qt5', silent=True)

        # Moc, uic and rcc write their version strings to stderr.
        # Moc and rcc return a non-zero result when doing so.
        # What kind of an idiot thought that was a good idea?
        if self.moc.found():
            mp = subprocess.Popen(self.moc.get_command() + ['-v'],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            (stdout, stderr) = mp.communicate()
            stdout = stdout.decode().strip()
            stderr = stderr.decode().strip()
            if 'Qt 5' in stderr:
                moc_ver = stderr
            elif '5.' in stdout:
                moc_ver = stdout
            else:
                raise DependencyException('Moc preprocessor is not for Qt 5. Output:\n%s\n%s' %
                                          (stdout, stderr))
            if not qt5toolinfo_printed:
                mlog.log(' moc:', mlog.green('YES'), '(%s, %s)' % \
                         (' '.join(self.moc.fullpath), moc_ver.split()[-1]))
        else:
            if not qt5toolinfo_printed:
                mlog.log(' moc:', mlog.red('NO'))
        if self.uic.found():
            up = subprocess.Popen(self.uic.get_command() + ['-v'],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            (stdout, stderr) = up.communicate()
            stdout = stdout.decode().strip()
            stderr = stderr.decode().strip()
            if 'version 5.' in stderr:
                uic_ver = stderr
            elif '5.' in stdout:
                uic_ver = stdout
            else:
                raise DependencyException('Uic compiler is not for Qt 5. Output:\n%s\n%s' %
                                          (stdout, stderr))
            if not qt5toolinfo_printed:
                mlog.log(' uic:', mlog.green('YES'), '(%s, %s)' % \
                         (' '.join(self.uic.fullpath), uic_ver.split()[-1]))
        else:
            if not qt5toolinfo_printed:
                mlog.log(' uic:', mlog.red('NO'))
        if self.rcc.found():
            rp = subprocess.Popen(self.rcc.get_command() + ['-v'],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            (stdout, stderr) = rp.communicate()
            stdout = stdout.decode().strip()
            stderr = stderr.decode().strip()
            if 'version 5.' in stderr:
                rcc_ver = stderr
            elif '5.' in stdout:
                rcc_ver = stdout
            else:
                raise DependencyException('Rcc compiler is not for Qt 5. Output:\n%s\n%s' %
                                          (stdout, stderr))
            if not qt5toolinfo_printed:
                mlog.log(' rcc:', mlog.green('YES'), '(%s, %s)'\
                        % (' '.join(self.rcc.fullpath), rcc_ver.split()[-1]))
        else:
            if not qt5toolinfo_printed:
                mlog.log(' rcc:', mlog.red('NO'))
        qt5toolinfo_printed = True

    def get_version(self):
        return self.modules[0].get_version()

    def get_compile_args(self):
        args = []
        for m in self.modules:
            args += m.get_compile_args()
        return args

    def get_sources(self):
        return []

    def get_link_args(self):
        args = []
        for module in self.modules:
            args += module.get_link_args()
        return args

    def found(self):
        if not self.moc.found():
            return False
        if not self.uic.found():
            return False
        if not self.rcc.found():
            return False
        for i in self.modules:
            if not i.found():
                return False
        return True

    def get_generate_rules(self):
        moc_rule = CustomRule(self.moc.get_command() + ['$mocargs', '@INFILE@', '-o', '@OUTFILE@'],
                              'moc_@BASENAME@.cpp', 'moc_headers', 'moc_hdr_compile',
                              'Compiling header @INFILE@ with the moc preprocessor')
        mocsrc_rule = CustomRule(self.moc.get_command() + ['$mocargs', '@INFILE@', '-o', '@OUTFILE@'],
                              '@BASENAME@.moc', 'moc_sources', 'moc_src_compile',
                              'Compiling source @INFILE@ with the moc preprocessor')
        ui_rule = CustomRule(self.uic.get_command() + ['@INFILE@', '-o', '@OUTFILE@'],
                              'ui_@BASENAME@.h', 'ui_files', 'ui_compile',
                              'Compiling @INFILE@ with the ui compiler')
        rrc_rule = CustomRule(self.rcc.get_command() + ['@INFILE@', '-o', '@OUTFILE@',
                               '${rcc_args}'], '@BASENAME@.cpp','qresources',
                              'rc_compile', 'Compiling @INFILE@ with the rrc compiler')
        return [moc_rule, mocsrc_rule, ui_rule, rrc_rule]

    def get_exe_args(self):
        # Qt5 seems to require this always.
        # Fix this to be more portable, especially to MSVC.
        return ['-fPIE']

class GnuStepDependency(Dependency):
    def __init__(self, kwargs):
        Dependency.__init__(self)
        self.modules = kwargs.get('modules', [])
        self.detect()

    def detect(self):
        confprog = 'gnustep-config'
        try:
            gp = subprocess.Popen([confprog, '--help'],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            gp.communicate()
        except FileNotFoundError:
            self.args = None
            mlog.log('Dependency GnuStep found:', mlog.red('NO'), '(no gnustep-config)')
            return
        if gp.returncode != 0:
            self.args = None
            mlog.log('Dependency GnuStep found:', mlog.red('NO'))
            return
        if 'gui' in self.modules:
            arg = '--gui-libs'
        else:
            arg = '--base-libs'
        fp = subprocess.Popen([confprog, '--objc-flags'],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        (flagtxt, flagerr) = fp.communicate()
        flagtxt = flagtxt.decode()
        flagerr = flagerr.decode()
        if fp.returncode != 0:
            raise DependencyException('Error getting objc-args: %s %s' % (flagtxt, flagerr))
        args = flagtxt.split()
        self.args = self.filter_arsg(args)
        fp = subprocess.Popen([confprog, arg],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        (libtxt, liberr) = fp.communicate()
        libtxt = libtxt.decode()
        liberr = liberr.decode()
        if fp.returncode != 0:
            raise DependencyException('Error getting objc-lib args: %s %s' % (libtxt, liberr))
        self.libs = self.weird_filter(libtxt.split())
        mlog.log('Dependency GnuStep found:', mlog.green('YES'))

    def weird_filter(self, elems):
        """When building packages, the output of the enclosing Make
is sometimes mixed among the subprocess output. I have no idea
why. As a hack filter out everything that is not a flag."""
        return [e for e in elems if e.startswith('-')]


    def filter_arsg(self, args):
        """gnustep-config returns a bunch of garbage args such
        as -O2 and so on. Drop everything that is not needed."""
        result = []
        for f in args:
            if f.startswith('-D') or f.startswith('-f') or \
            f.startswith('-I') or f == '-pthread' or\
            (f.startswith('-W') and not f == '-Wall'):
                result.append(f)
        return result

    def found(self):
        return self.args is not None

    def get_compile_args(self):
        if self.args is None:
            return []
        return self.args

    def get_link_args(self):
        return self.libs

class AppleFrameworks(Dependency):
    def __init__(self, kwargs):
        Dependency.__init__(self)
        modules = kwargs.get('modules', [])
        if isinstance(modules, str):
            modules = [modules]
        if len(modules) == 0:
            raise DependencyException("AppleFrameworks dependency requires at least one module.")
        self.frameworks = modules

    def get_link_args(self):
        args = []
        for f in self.frameworks:
            args.append('-framework')
            args.append(f)
        return args

    def found(self):
        return mesonlib.is_osx()

class ExtraFrameworkDependency(Dependency):
    def __init__(self, name, required):
        Dependency.__init__(self)
        self.name = None
        self.detect(name)
        if self.found():
            mlog.log('Dependency', mlog.bold(name), 'found:', mlog.green('YES'), os.path.join(self.path, self.name))
        else:
            mlog.log('Dependency', name, 'found:', mlog.red('NO'))

    def detect(self, name):
        lname = name.lower()
        paths = ['/Library/Frameworks']
        for p in paths:
            for d in os.listdir(p):
                fullpath = os.path.join(p, d)
                if lname != d.split('.')[0].lower():
                    continue
                if not stat.S_ISDIR(os.stat(fullpath).st_mode):
                    continue
                self.path = p
                self.name = d
                return

    def get_compile_args(self):
        return ['-I' + os.path.join(self.path, self.name, 'Headers')]

    def get_link_args(self):
        return ['-F' + self.path, '-framework', self.name.split('.')[0]]

    def found(self):
        return self.name is not None

def get_dep_identifier(name, kwargs):
    elements = [name]
    modlist = kwargs.get('modules', [])
    if isinstance(modlist, str):
        modlist = [modlist]
    for module in modlist:
        elements.append(module)
    return '/'.join(elements)

def find_external_dependency(name, kwargs):
    required = kwargs.get('required', True)
    if not isinstance(required, bool):
        raise DependencyException('Keyword "required" must be a boolean.')
    if name in packages:
        dep = packages[name](kwargs)
        if required and not dep.found():
            raise DependencyException('Dependency "%s" not found' % name)
        return dep
    pkg_exc = None
    pkgdep = None
    try:
        pkgdep = PkgConfigDependency(name, kwargs)
        if pkgdep.found():
            return pkgdep
    except Exception as e:
        pkg_exc = e
    if mesonlib.is_osx():
        return ExtraFrameworkDependency(name, required)
    if pkg_exc is not None:
        raise pkg_exc
    return pkgdep

# This has to be at the end so the classes it references
# are defined.
packages = {'boost': BoostDependency,
            'gtest': GTestDependency,
            'gmock': GMockDependency,
            'qt5': Qt5Dependency,
            'Qt5': Qt5Dependency, # Qt people sure do love their upper case.
            'gnustep': GnuStepDependency,
            'appleframeworks': AppleFrameworks,
            'wxwidgets' : WxDependency,
            }
