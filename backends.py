# Copyright 2012-2014 The Meson development team

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import mparser
import os, re, pickle
import build
from coredata import MesonException

def do_replacement(regex, line, confdata):
    match = re.search(regex, line)
    while match:
        varname = match.group(1)
        if varname in confdata.keys():
            var = confdata.get(varname)
            if isinstance(var, str):
                pass
            elif isinstance(var, mparser.StringNode):
                var = var.value
            elif isinstance(var, int):
                var = str(var)
            else:
                raise RuntimeError('Tried to replace a variable with something other than a string or int.')
        else:
            var = ''
        line = line.replace('@' + varname + '@', var)
        match = re.search(regex, line)
    return line

def do_mesondefine(line, confdata):
    arr = line.split()
    if len(arr) != 2:
        raise build.InvalidArguments('#mesondefine does not contain exactly two tokens: %s', line.strip())
    varname = arr[1]
    try:
        v = confdata.get(varname)
    except KeyError:
        return '/* undef %s */\n' % varname
    if isinstance(v, mparser.BooleanNode):
        v = v.value
    if isinstance(v, bool):
        if v:
            return '#define %s\n' % varname
        else:
            return '#undef %s\n' % varname
    elif isinstance(v, int):
        return '#define %s %d\n' % (varname, v)
    elif isinstance(v, str):
        return '#define %s %s\n' % (varname, v)
    else:
        raise build.InvalidArguments('#mesondefine argument "%s" is of unknown type.' % varname)

def replace_if_different(dst, dst_tmp):
    # If contents are identical, don't touch the file to prevent
    # unnecessary rebuilds.
    try:
        if open(dst, 'r').read() == open(dst_tmp, 'r').read():
            os.unlink(dst_tmp)
            return
    except FileNotFoundError:
        pass
    os.replace(dst_tmp, dst)

def do_conf_file(src, dst, confdata):
    data = open(src).readlines()
    regex = re.compile('@(.*?)@')
    result = []
    for line in data:
        if line.startswith('#mesondefine'):
            line = do_mesondefine(line, confdata)
        else:
            line = do_replacement(regex, line, confdata)
        result.append(line)
    dst_tmp = dst + '~'
    open(dst_tmp, 'w').writelines(result)
    replace_if_different(dst, dst_tmp)

class TestSerialisation:
    def __init__(self, name, fname, is_cross, exe_wrapper, is_parallel, cmd_args, env,
                 valgrind_args):
        self.name = name
        self.fname = fname
        self.is_cross = is_cross
        self.exe_runner = exe_wrapper
        self.is_parallel = is_parallel
        self.cmd_args = cmd_args
        self.env = env
        self.valgrind_args = valgrind_args

# This class contains the basic functionality that is needed by all backends.
# Feel free to move stuff in and out of it as you see fit.
class Backend():
    def __init__(self, build, interp):
        self.build = build
        self.environment = build.environment
        self.interpreter = interp
        self.processed_targets = {}
        self.dep_rules = {}
        self.build_to_src = os.path.relpath(self.environment.get_source_dir(),
                                            self.environment.get_build_dir())

    def get_compiler_for_lang(self, lang):
        for i in self.build.compilers:
            if i.language == lang:
                return i
        raise RuntimeError('No compiler for language ' + lang)

    def get_compiler_for_source(self, src):
        for i in self.build.compilers:
            if i.can_compile(src):
                return i
        raise RuntimeError('No specified compiler can handle file ' + src)

    def get_target_filename(self, target):
        targetdir = self.get_target_dir(target)
        fname = target.get_filename()
        if isinstance(fname, list):
            fname = fname[0] # HORROR, HORROR! Fix this.
        filename = os.path.join(targetdir, fname)
        return filename

    def get_target_dir(self, target):
        dirname = target.get_subdir()
        os.makedirs(os.path.join(self.environment.get_build_dir(), dirname), exist_ok=True)
        return dirname

    def get_target_private_dir(self, target):
        dirname = os.path.join(self.get_target_dir(target), target.get_basename() + '.dir')
        os.makedirs(os.path.join(self.environment.get_build_dir(), dirname), exist_ok=True)
        return dirname

    def generate_unity_files(self, target, unity_src):
        langlist = {}
        abs_files = []
        result = []
        for src in unity_src:
            comp = self.get_compiler_for_source(src)
            language = comp.get_language()
            suffix = '.' + comp.get_default_suffix()
            if language not in langlist:
                outfilename = os.path.join(self.get_target_private_dir(target), target.name + '-unity' + suffix)
                outfileabs = os.path.join(self.environment.get_build_dir(), outfilename)
                outfileabs_tmp = outfileabs + '.tmp'
                abs_files.append(outfileabs)
                outfile = open(outfileabs_tmp, 'w')
                langlist[language] = outfile
                result.append(outfilename)
            ofile = langlist[language]
            ofile.write('#include<%s>\n' % src)
        [x.close() for x in langlist.values()]
        [replace_if_different(x, x + '.tmp') for x in abs_files]
        return result

    def relpath(self, todir, fromdir):
        return os.path.relpath(os.path.join('dummyprefixdir', todir),\
                               os.path.join('dummyprefixdir', fromdir))

    def flatten_object_list(self, target, proj_dir_to_build_root=''):
        obj_list = []
        for obj in target.get_objects():
            if isinstance(obj, str):
                o = os.path.join(proj_dir_to_build_root,
                                 self.build_to_src, target.get_subdir(), obj)
                obj_list.append(o)
            elif isinstance(obj, build.ExtractedObjects):
                obj_list += self.determine_ext_objs(obj, proj_dir_to_build_root)
            else:
                raise MesonException('Unknown data type in object list.')
        return obj_list

    def serialise_tests(self):
        test_data = os.path.join(self.environment.get_scratch_dir(), 'meson_test_setup.dat')
        datafile = open(test_data, 'wb')
        self.write_test_file(datafile)
        datafile.close()

    def has_vala(self, target):
        for s in target.get_sources():
            if s.endswith('.vala'):
                return True
        return False

    def has_rust(self, target):
        for s in target.get_sources():
            if s.endswith('.rs'):
                return True
        return False

    def has_cs(self, target):
        for s in target.get_sources():
            if s.endswith('.cs'):
                return True
        return False

    def determine_linker(self, target, src):
        if isinstance(target, build.StaticLibrary):
            return self.build.static_linker
        if len(self.build.compilers) == 1:
            return self.build.compilers[0]
        # Currently a bit naive. C++ must
        # be linked with a C++ compiler, but
        # otherwise we don't care. This will
        # become trickier if and when Fortran
        # and the like become supported.
        cpp = None
        for c in self.build.compilers:
            if c.get_language() == 'cpp':
                cpp = c
                break
        if cpp is not None:
            for s in src:
                if c.can_compile(s):
                    return cpp
        for c in self.build.compilers:
            if c.get_language() != 'vala':
                return c
        raise RuntimeError('Unreachable code')

    def determine_ext_objs(self, extobj, proj_dir_to_build_root=''):
        result = []
        targetdir = self.get_target_private_dir(extobj.target)
        suffix = '.' + self.environment.get_object_suffix()
        for osrc in extobj.srclist:
            if not self.source_suffix_in_objs:
                osrc = '.'.join(osrc.split('.')[:-1])
            objname = os.path.join(proj_dir_to_build_root,
                                   targetdir, os.path.basename(osrc) + suffix)
            result.append(objname)
        return result

    def get_pch_include_args(self, compiler, target):
        args = []
        pchpath = self.get_target_private_dir(target)
        includeargs = compiler.get_include_args(pchpath)
        for lang in ['c', 'cpp']:
            p = target.get_pch(lang)
            if len(p) == 0:
                continue
            if compiler.can_compile(p[-1]):
                header = p[0]
                args += compiler.get_pch_use_args(pchpath, header)
        if len(args) > 0:
            args = includeargs + args
        return args

    def generate_basic_compiler_args(self, target, compiler):
        commands = []
        commands += compiler.get_always_args()
        commands += self.build.get_global_args(compiler)
        commands += self.environment.coredata.external_args[compiler.get_language()]
        commands += target.get_extra_args(compiler.get_language())
        if self.environment.coredata.buildtype != 'plain':
            commands += compiler.get_std_warn_args()
        commands += compiler.get_buildtype_args(self.environment.coredata.buildtype)
        if self.environment.coredata.coverage:
            commands += compiler.get_coverage_args()
        if self.environment.coredata.werror:
            commands += compiler.get_werror_args()
        if isinstance(target, build.SharedLibrary):
            commands += compiler.get_pic_args()
        for dep in target.get_external_deps():
            commands += dep.get_compile_args()
            if isinstance(target, build.Executable):
                commands += dep.get_exe_args()

        return commands

    def build_target_link_arguments(self, compiler, deps):
        args = []
        for d in deps:
            if not isinstance(d, build.StaticLibrary) and\
            not isinstance(d, build.SharedLibrary):
                raise RuntimeError('Tried to link with a non-library target "%s".' % d.get_basename())
            fname = self.get_target_filename(d)
            if compiler.id == 'msvc':
                if fname.endswith('dll'):
                    fname = fname[:-3] + 'lib'
            args.append(fname)
            # If you have executable e that links to shared lib s1 that links to shared library s2
            # you have to specify s2 as well as s1 when linking e even if e does not directly use
            # s2. Gcc handles this case fine but Clang does not for some reason. Thus we need to
            # explictly specify all libraries every time.
            args += self.build_target_link_arguments(compiler, d.get_dependencies())
        return args

    def generate_configure_files(self):
        for cf in self.build.get_configure_files():
            infile = os.path.join(self.environment.get_source_dir(),
                                  cf.get_subdir(),
                                  cf.get_source_name())
            outdir = os.path.join(self.environment.get_build_dir(),
                                   cf.get_subdir())
            os.makedirs(outdir, exist_ok=True)
            outfile = os.path.join(outdir, cf.get_target_name())
            confdata = cf.get_configuration_data()
            do_conf_file(infile, outfile, confdata)

    def write_test_file(self, datafile):
        arr = []
        for t in self.build.get_tests():
            fname = os.path.join(self.environment.get_build_dir(), self.get_target_filename(t.get_exe()))
            is_cross = self.environment.is_cross_build()
            if is_cross:
                exe_wrapper = self.environment.cross_info.get('exe_wrapper', None)
            else:
                exe_wrapper = None
            ts = TestSerialisation(t.get_name(), fname, is_cross, exe_wrapper,
                                   t.is_parallel, t.cmd_args, t.env, t.valgrind_args)
            arr.append(ts)
        pickle.dump(arr, datafile)

    def generate_pkgconfig_files(self):
        for p in self.build.pkgconfig_gens:
            outdir = self.environment.scratch_dir
            fname = os.path.join(outdir, p.filebase + '.pc')
            ofile = open(fname, 'w')
            ofile.write('prefix=%s\n' % self.environment.get_coredata().prefix)
            ofile.write('libdir=${prefix}/%s\n' % self.environment.get_coredata().libdir)
            ofile.write('includedir=${prefix}/%s\n\n' % self.environment.get_coredata().includedir)
            ofile.write('Name: %s\n' % p.name)
            if len(p.description) > 0:
                ofile.write('Description: %s\n' % p.description)
            if len(p.version) > 0:
                ofile.write('Version: %s\n' % p.version)
            ofile.write('Libs: -L${libdir} ')
            for l in p.libraries:
                ofile.write('-l%s ' % l.name)
            ofile.write('\n')
            ofile.write('CFlags: ')
            for h in p.subdirs:
                if h == '.':
                    h = ''
                ofile.write(os.path.join('-I${includedir}', h))
                ofile.write(' ')
            ofile.write('\n')

