"""KBQA 抽取式问答演示：Flask 入口。

启动：python app.py  （默认 http://127.0.0.1:8765/kbqa）
只注册知识库问答蓝图，不包含其他场景。
"""
from __future__ import annotations

from flask import Flask, jsonify, redirect

from cls.scenarios.kbqa.web import kbqa_bp


def create_app() -> Flask:
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.register_blueprint(kbqa_bp)

    @app.get("/")
    def index():
        return redirect("/kbqa")

    @app.get("/api/ping")
    def ping():
        return jsonify({"app": "kbqa-citation-demo", "ok": True})

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8765)
