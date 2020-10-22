#
# Copyright (c) 2016 Alex Richardson
# All rights reserved.
#
# This software was developed by SRI International and the University of
# Cambridge Computer Laboratory under DARPA/AFRL contract FA8750-10-C-0237
# ("CTSRD"), as part of the DARPA CRASH research programme.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
# 1. Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE AUTHOR AND CONTRIBUTORS ``AS IS'' AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE AUTHOR OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS
# OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
# HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY
# OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF
# SUCH DAMAGE.
#
import shutil
import sys
import typing
from pathlib import Path

from .crosscompileproject import (BuildType, CheriConfig, CompilationTargets, CrossCompileAutotoolsProject,
                                  DefaultInstallDir, GitRepository, Linkage, MakeCommandKind)
from ..project import TargetBranchInfo
from ...utils import OSInfo, run_command, status_update


class TemporarilyRemoveProgramsFromSdk(object):
    def __init__(self, programs: "typing.List[str]", config: CheriConfig, sdk_bindir: Path):
        self.programs = programs
        self.config = config
        self.sdk_bindir = sdk_bindir

    def __enter__(self):
        status_update('Temporarily moving', self.programs, "from", self.sdk_bindir)
        for prog in self.programs:
            if (self.sdk_bindir / prog).exists():
                run_command("mv", "-f", prog, prog + ".backup", cwd=self.sdk_bindir, print_verbose_only=True)
        return self

    def __exit__(self, *exc):
        status_update('Restoring', self.programs, "in", self.sdk_bindir)
        for prog in self.programs:
            if (self.sdk_bindir / (prog + ".backup")).exists() or self.config.pretend:
                run_command("mv", "-f", prog + ".backup", prog, cwd=self.sdk_bindir, print_verbose_only=True)
        return False


class BuildGDB(CrossCompileAutotoolsProject):
    path_in_rootfs = "/usr/local"  # Always install gdb as /usr/local/bin/gdb
    native_install_dir = DefaultInstallDir.CHERI_SDK
    cross_install_dir = DefaultInstallDir.ROOTFS
    repository = GitRepository(
        "https://github.com/CTSRD-CHERI/gdb.git",
        # Branch name is changed for every major GDB release:
        default_branch="mips_cheri-8.3", force_branch=True,
        per_target_branches={
            CompilationTargets.CHERIBSD_AARCH64: TargetBranchInfo(branch="morello-8.3",
                                                                  directory_name="morello-gdb"),
            CompilationTargets.CHERIBSD_MORELLO_HYBRID: TargetBranchInfo(branch="morello-8.3",
                                                                         directory_name="morello-gdb"),
            },
        old_urls=[b'https://github.com/bsdjhb/gdb.git'])
    make_kind = MakeCommandKind.GnuMake
    is_sdk_target = True
    default_build_type = BuildType.RELEASE
    supported_architectures = [CompilationTargets.NATIVE,
                               CompilationTargets.CHERIBSD_MIPS_HYBRID, CompilationTargets.CHERIBSD_RISCV_HYBRID,
                               CompilationTargets.CHERIBSD_MIPS_NO_CHERI, CompilationTargets.CHERIBSD_RISCV_NO_CHERI,
                               CompilationTargets.CHERIBSD_AARCH64, CompilationTargets.CHERIBSD_MORELLO_HYBRID]
    default_architecture = CompilationTargets.NATIVE

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)

    def __init__(self, config: CheriConfig):
        self._compile_status_message = None
        if not self._xtarget.is_native():
            # We always want to build the MIPS binary static so we can just scp it over to QEMU
            self._linkage = Linkage.STATIC

        super().__init__(config)
        assert not self.compiling_for_cheri(), "Should only build this as a static MIPS binary not CHERIABI"

    def setup(self):
        super().setup()
        install_root = self.install_dir if self.compiling_for_host() else self.install_prefix
        # See https://github.com/bsdjhb/kdbg/blob/master/gdb/build
        # ./configure flags
        self.configure_args.extend([
            "--disable-nls",
            "--enable-tui",
            "--enable-ld=yes" if self.compiling_for_host() else "--disable-ld",
            "--disable-gold",
            "--enable-64-bit-bfd",
            "--without-gnu-as",
            "--mandir=" + str(install_root / "man"),
            "--infodir=" + str(install_root / "info"),
            # "--disable-sim",
            "--disable-werror",
            "MAKEINFO=" + str(shutil.which("false")),
            "--with-gdb-datadir=" + str(install_root / "share/gdb"),
            "--disable-libstdcxx",
            "--with-guile=no",
            ])

        if self.compiling_for_host() and self.target_info.is_macos():
            # Allow building ld.bfd by building for FreeBSD
            self.configure_args.append("--target=x86_64-unknown-freebsd13")

        # BUILD the gui:
        if False and self.compiling_for_host():
            self.configure_args.append("--enable-gdbtk")
            # if OSInfo.IS_MAC:
            # self.configure_args.append("--with-tcl=/usr/local/opt/tcl-tk/lib")
            # self.configure_environment["PKG_CONFIG_PATH"] =
            # "/usr/local/opt/tcl-tk/lib/pkgconfig:/usr/local/lib/pkgconfig"

        # extra ./configure environment variables:
        # compile flags
        # self.cross_warning_flags.extend(["-Wno-absolute-value", "-Wno-parentheses-equality"
        #                                   "-Wno-unused-function", "-Wno-unused-variable"])
        # These warnings are really noisy and useless:
        self.common_warning_flags.extend([
            "-Wno-mismatched-tags",
            "-Wno-unknown-warning-option",  # caused by the build passing -Wshadow=local
            "-Werror=implicit-function-declaration"
            ])
        self.CXXFLAGS.append("-Wno-mismatched-tags")
        if self.should_include_debug_info:
            self.COMMON_FLAGS.append("-g")
        self.COMMON_FLAGS.append("-fcommon")
        # TODO: we should fix this:
        self.cross_warning_flags.append("-Wno-error=format")
        self.cross_warning_flags.append("-Wno-error=incompatible-pointer-types")
        self.configure_args.append("--enable-targets=all")
        if self.compiling_for_host():
            self.LDFLAGS.append("-L/usr/local/lib")
            self.configure_args.append("--with-expat")
            self.configure_args.append("--with-python=" + str(sys.executable))
        else:
            self.configure_args.extend(["--without-python", "--without-expat", "--without-libunwind-ia64"])
            self.configure_environment.update(gl_cv_func_gettimeofday_clobber="no",
                                              lt_cv_sys_max_cmd_len="262144",
                                              # The build system run CC without any flags to detect dependency style...
                                              # (ZW_PROG_COMPILER_DEPENDENCIES([CC])) -> for gcc3 mode which seems
                                              # correct
                                              am_cv_CC_dependencies_compiler_type="gcc3",
                                              MAKEINFO="/bin/false"
                                              )
            self.COMMON_FLAGS.append("-static")  # seems like LDFLAGS is not enough
            # XXX: libtool wants to strip -static from some linker invocations,
            #      and because sbrk's availability is determined based on
            #      -static (libc.a has sbrk on RISC-V, but not libc.so.7), the
            #      dynamic links error with the missing symbol. --static isn't
            #      recognised by libtool but still accepted by the drivers, so
            #      this bypasses that.
            self.LDFLAGS.append("--static")
            self.COMMON_FLAGS.extend(["-DRL_NO_COMPAT", "-DLIBICONV_PLUG", "-fno-strict-aliasing"])
            # Currently there are a lot of `undefined symbol 'elf_version'`, etc errors
            # Add -lelf to the linker command line until the source is fixed
            self.LDFLAGS.append("-lelf")
            self.configure_environment.update(CONFIGURED_M4="m4", CONFIGURED_BISON="byacc", TMPDIR="/tmp", LIBS="")
        if self.make_args.command == "gmake":
            self.configure_environment["MAKE"] = "gmake"

        self.configure_environment["CC_FOR_BUILD"] = str(self.host_CC)
        self.configure_environment["CXX_FOR_BUILD"] = str(self.host_CXX)
        self.configure_environment["CFLAGS_FOR_BUILD"] = "-g -fcommon"
        self.configure_environment["CXXFLAGS_FOR_BUILD"] = "-g -fcommon"

        if not self.compiling_for_host():
            self.add_configure_env_arg("AR", self.sdk_bindir / "ar")
            self.add_configure_env_arg("RANLIB", self.sdk_bindir / "ranlib")
            self.add_configure_env_arg("NM", self.sdk_bindir / "nm")

        # Some of the configure scripts are invoked lazily (during the make invocation instead of from ./configure)
        # Therefore we need to set all the enviroment variables when compiling, too.
        self.make_args.set_env(**self.configure_environment)

    def configure(self, **kwargs):
        if self.compiling_for_host() and OSInfo.IS_MAC:
            self.configure_environment.clear()
            print(self.configure_args)
            # self.configure_args.clear()
        super().configure()

    def compile(self, **kwargs):
        with TemporarilyRemoveProgramsFromSdk(["as", "ld", "objcopy", "objdump"], self.config,
                                              self.install_dir):
            # also install objdump
            self.run_make(make_target="all-binutils", cwd=self.build_dir)
            self.run_make(make_target="all-gdb", cwd=self.build_dir)
            # And for native GDB also build ld.bfd
            if self.compiling_for_host():
                self.run_make(make_target="all-ld", cwd=self.build_dir)

    def install(self, **kwargs):
        self.run_make_install(target="install-gdb")
        if self.target_info.is_cheribsd() and self.compiling_for_cheri_hybrid():
            # If we are building a hybrid GDB, also install it to the purecap rootfs
            make_install_env = self.make_install_env.copy()
            purecap_target = self.crosscompile_target.get_cheri_purecap_target()
            rootfs_project = self.target_info.get_rootfs_project(xtarget=purecap_target)
            purecap_rootfs = rootfs_project.install_dir
            if (purecap_rootfs / "usr").exists():
                self.info("Also installing to", purecap_rootfs)
                assert "DESTDIR" in make_install_env, "DESTDIR must be set in install"
                make_install_env["DESTDIR"] = str(purecap_rootfs)
                self.run_make_install(target="install-gdb", make_install_env=make_install_env)
            else:
                self.info("Not installing to purecap rootfs", purecap_rootfs, "since it doesn't exist")
        # Install the binutils prefixed with g (like homebrew does it on MacOS)
        # objdump is useful for cases where CHERI llvm-objdump doesn't print sensible source lines
        # Also install most of the other tools in case they work better than elftoolchain
        if self.compiling_for_host():
            binutils = ("objdump", "objcopy", "addr2line", "readelf", "ar", "ranlib", "size", "strings")
            bindir = self.install_dir / "bin"
            for util in binutils:
                self.install_file(self.build_dir / "binutils" / util, bindir / ("g" + util))
            # nm and c++filt have a different name in the build dir:
            self.install_file(self.build_dir / "binutils/cxxfilt", bindir / "gc++filt")
            self.install_file(self.build_dir / "binutils/nm-new", bindir / "gnm")
            self.install_file(self.build_dir / "binutils/strip-new", bindir / "gstrip")
            self.install_file(self.build_dir / "ld/ld-new", bindir / "ld.bfd")
            self.install_file(self.build_dir / "ld/ld-new", bindir / "gld.bfd")


class BuildKGDB(BuildGDB):
    repository = GitRepository("https://github.com/CTSRD-CHERI/gdb.git",
                               # Branch name is changed for every major GDB release:
                               default_branch="mips_cheri-8.3-kgdb", force_branch=True,
                               old_urls=[b'https://github.com/bsdjhb/gdb.git'])
