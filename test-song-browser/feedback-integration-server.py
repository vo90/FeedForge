"""Test-owned launcher for the installed, unchanged FeedBack backend.

Only identity/shutdown instrumentation is added. Library, scanner, highway and
audio routes are the installed app's real routes. No Desktop process is started.
"""

import hashlib
import json
import os
from pathlib import Path
import sys


def main():
    spec_path = Path(sys.argv[1]).resolve(strict=True)
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    root = Path(spec["runtime"]).resolve(strict=True)
    assert spec_path.parent == root
    assert Path(sys.executable).resolve() == Path(spec["python"]).resolve()
    source = Path(spec["source"]).resolve(strict=True)
    assert source.joinpath("VERSION").read_text(encoding="utf-8").strip() == spec["version"]
    for filename, expected in spec["sourceFiles"].items():
        assert hashlib.sha256(source.joinpath(filename).read_bytes()).hexdigest() == expected
    for variable, expected in spec["isolation"].items():
        assert os.environ.get(variable) == expected, variable
    assert sys.dont_write_bytecode
    assert os.environ["FEEDBACK_SKIP_STARTUP_TASKS"] == "1"
    assert os.environ["FEEDBACK_ENRICH_OFFLINE"] == "1"
    assert os.environ["FEEDBACK_MAX_SCAN_WORKERS"] == "1"
    assert not list(Path(os.environ["FEEDBACK_PLUGINS_DIR"]).iterdir())

    import server
    import plugins
    import uvicorn

    assert Path(server.__file__).resolve() == source / "server.py"
    assert Path(server.CONFIG_DIR).resolve() == root / "profile"
    assert Path(server._get_dlc_dir()).resolve() == Path(spec["effectiveLibrary"]).resolve()

    config = uvicorn.Config(server.app, host="127.0.0.1", port=spec["port"],
                            log_config=None, access_log=False, ws_max_size=65536)
    runner = uvicorn.Server(config)

    @server.app.get("/__feedforge_smoke_identity/" + spec["identityToken"])
    def identity():
        with plugins.PLUGINS_LOCK:
            plugin_count = len(plugins.LOADED_PLUGINS) + len(plugins.PENDING_PLUGINS)
        return {"pid": os.getpid(), "root": str(root), "source": str(source),
                "python": str(Path(sys.executable).resolve()),
                "configDir": str(server.CONFIG_DIR.resolve()),
                "effectiveLibrary": str(server._get_dlc_dir().resolve()),
                "pluginCount": plugin_count, "startupSkipped": True}

    @server.app.post("/__feedforge_smoke_stop/" + spec["identityToken"])
    def stop():
        runner.should_exit = True
        return {"stopping": True}

    runner.run()


if __name__ == "__main__":
    main()
