# pyright: strict, reportTypeCommentUsage=false, reportMissingTypeStubs=false
"""Build-time conda env installer for `--environment=prebuilt`.

Runs INSIDE the Cloudbuild docker build, with the MetaflowPackage
tarball already extracted at `cwd`. The runtime task entry_point does
exactly the same setup before invoking `remote_bootstrap.bootstrap_environment`
— we just call `Conda.create_for_step` directly with the slightly
different settings the build container needs.

What the Dockerfile sets up for us:

- `METAFLOW_CONDA_REMOTE_INSTALLER=""` → `Conda._ensure_remote_conda`'s
  truthy check drops into `_ensure_micromamba` (public download from
  micro.mamba.pm). No AWS creds required.
- `METAFLOW_DATASTORE_SYSROOT_LOCAL=<cwd>` → `LocalStorage` reads the
  resolved-env manifest from `<cwd>/.metaflow/<CONDA_MAGIC_FILE_V2>`.
  We call `setup_conda_manifest()` (reused from `remote_bootstrap`)
  to move the manifest from its packed location to that path.
- `METAFLOW_EXTRACTED_ROOT=<cwd>` → activates the `.mf_install` marker
  bypass in `metaflow.extension_support`, so the flattened nflx-*
  distributions in the code package load cleanly without tripping
  the path walker's per-distribution checks.
- `PYTHONPATH=<cwd>/.mf_code` → metaflow + all extensions resolve from
  the extracted code package.
- `MAMBA_ROOT_PREFIX=<conda-root>` → micromamba creates the env at the
  deterministic runtime-shared path.

What's left for the shim:

1.  `setup_conda_manifest()` — move the conda manifest from the
    packaging-internal location to the LocalStorage path. Identical
    call to the one `remote_bootstrap.bootstrap_environment` makes.
2.  `Conda(echo, "local", mode="remote")` — `datastore_type="local"`
    makes `Conda._storage = None`, which (after the upstream storage
    guards in `_create` + `lazy_fetch_packages`) disables cache_info
    URL emission and forces web downloads from `pkg.url`. Those
    URLs go to conda.netflix.net / pypi.netflix.net over HTTPS.
3.  `create_for_step` — same call the runtime makes via
    `remote_bootstrap.bootstrap_environment`.
"""
import os
import sys
import time

from metaflow.cli import echo_always

from .conda import Conda
from .env_descr import EnvID
from .remote_bootstrap import setup_conda_manifest
from .utils import arch_id


def _echo(*args, **kwargs):
    kwargs["err"] = False
    echo_always(*args, **kwargs)


def install_env(req_id: str, full_id: str) -> str:
    start = time.time()
    _echo("    Setting up Conda (build-time) ...", nl=False)

    setup_conda_manifest()

    my_conda = Conda(_echo, "local", mode="remote")
    my_conda.binary("micromamba")
    _echo(" done in %ds." % int(time.time() - start))

    env_id = EnvID(req_id=req_id, full_id=full_id, arch=arch_id())
    resolved_env = my_conda.environment(env_id)
    if resolved_env is None:
        raise RuntimeError(
            "Cannot find cached environment for hash %s:%s in build-time "
            "manifest. Verify the MetaflowPackage tarball was extracted at "
            "the WORKDIR and that setup_conda_manifest moved the manifest "
            "into place." % (req_id, full_id)
        )

    install_start = time.time()
    env_path = my_conda.create_for_step(
        "prebuilt_build", resolved_env, do_symlink=False
    )
    _echo(
        "    Env installed at %s (%ds)" % (env_path, int(time.time() - install_start))
    )
    return env_path


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(
            "Usage: python -m metaflow_extensions.prebuilt.plugins.conda."
            "prebuilt_build_install <req_id> <full_id>",
            file=sys.stderr,
        )
        sys.exit(2)
    path = install_env(sys.argv[1], sys.argv[2])
    print(path)
    sys.stdout.flush()
    os._exit(0)
