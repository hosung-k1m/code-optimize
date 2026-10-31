"""Serve actual plugin renderers against installed-run evidence for visual inspection."""

import json
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from slimconfig import Config, load_config


@dataclass
class PreviewConfig(Config):
    host: str
    port: int
    evidence_directory: str
    kit_directory: str


ROOT = Path(__file__).parents[1]
PAGE = b'''<!doctype html><meta charset="utf-8"><title>Code Optimize renderer check</title>
<link rel="stylesheet" href="/kit/kit.css"><link rel="stylesheet" href="/renderers.css">
<style>body{max-width:1100px;margin:32px auto;padding:24px;background:#f8faf9;color:#172b27;font:16px system-ui}article{background:white;padding:24px;border:1px solid #d9e4df;border-radius:12px;margin-block:24px}</style>
<h1>Code Optimize</h1><div id="results"></div><script type="module">
import renderers from '/renderers.js';
const data = await (await fetch('/evidence.json')).json();
for (const [name, value] of Object.entries(data)) {
 const article=document.createElement('article'); article.append(renderers[name](value,{verdict:{message:'Checked with the installed deterministic verifier'}}));
 document.querySelector('#results').append(article);
}
window.rendererCheck = {complete: true, tables: document.querySelectorAll('table').length, error: null};
</script>'''


def main():
    cfg = load_config(PreviewConfig, [str(Path(__file__).with_name("renderer_preview.yaml"))])
    evidence = (ROOT / cfg.evidence_directory).resolve()
    kit = (ROOT / cfg.kit_directory).resolve()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/":
                content, mime = PAGE, "text/html"
            elif self.path == "/evidence.json":
                described = json.loads((evidence / "runs/describe-repeated-shortest-paths/returned-output.json").read_text())
                output = json.loads((evidence / "runs/cpp/returned-output.json").read_text())
                listing = json.loads((evidence / "runs/list/returned-output.json").read_text())
                content, mime = json.dumps({"ListProblemsOutput": listing, "DescribeOutput": described, "EvaluateOutput": output}).encode(), "application/json"
            elif self.path in ("/renderers.js", "/renderers.css"):
                path = ROOT / "ui" / self.path[1:]
                content, mime = path.read_bytes(), "text/javascript" if path.suffix == ".js" else "text/css"
            elif self.path.startswith("/kit/"):
                relative = Path(self.path[5:])
                if relative.is_absolute() or ".." in relative.parts:
                    self.send_error(400)
                    return
                path = kit / relative
                if not path.is_file():
                    self.send_error(404)
                    return
                content, mime = path.read_bytes(), "text/javascript" if path.suffix == ".js" else "text/css"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.end_headers()
            self.wfile.write(content)

    HTTPServer((cfg.host, cfg.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
