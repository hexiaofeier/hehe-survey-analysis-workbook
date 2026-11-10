"""Render a Markdown analysis brief as a self-contained scrolling HTML file.

Requires: markdown, beautifulsoup4. No other Skill or browser is required to render.
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import mimetypes
import os
from pathlib import Path
import re
from urllib.parse import quote, unquote, urlsplit


def _local_path(value: str, folder: Path) -> tuple[Path | None, str, str]:
    """Return a local path, query and fragment, or None for ordinary URL links."""
    if re.match(r"^[A-Za-z]:[\\/]", value):
        return Path(value), "", ""
    parsed = urlsplit(value)
    if parsed.scheme == "file":
        name = unquote(parsed.path)
        if os.name == "nt" and re.match(r"^/[A-Za-z]:/", name):
            name = name[1:]
        if parsed.netloc:
            name = "//" + parsed.netloc + name
        return Path(name), parsed.query, parsed.fragment
    if parsed.scheme or parsed.netloc or value.startswith("#"):
        return None, parsed.query, parsed.fragment
    return (folder / unquote(parsed.path)).resolve(), parsed.query, parsed.fragment


def render(source: str | Path, output: str | Path, force: bool = False) -> dict:
    try:
        import markdown
        from bs4 import BeautifulSoup
    except ImportError as exc:
        raise RuntimeError(
            "生成 HTML 需要 markdown 和 beautifulsoup4："
            "python -m pip install markdown beautifulsoup4"
        ) from exc

    source = Path(source).resolve()
    output = Path(output).resolve()
    if source == output:
        raise ValueError("HTML 输出必须另存，不能覆盖 Markdown 原稿。")
    if output.exists() and not force:
        raise FileExistsError(f"输出已存在，请另取文件名或明确使用 --force：{output}")
    document = markdown.markdown(
        source.read_text(encoding="utf-8-sig"),
        extensions=["tables", "fenced_code", "footnotes", "toc", "sane_lists"],
    )
    soup = BeautifulSoup(document, "html.parser")
    if soup.find(["script", "style", "iframe", "link", "object", "embed"]):
        raise ValueError("Markdown 含脚本、全局样式或嵌入网页，请保留正文及本地图片后生成。")
    for node in soup.find_all(True):
        if any(str(key).lower().startswith("on") for key in node.attrs):
            raise ValueError("正文 HTML 含事件脚本，请移除后生成。")

    embedded_images = 0
    for image in soup.find_all("img"):
        src = image.get("src", "")
        if src.startswith("data:"):
            continue
        asset, _, _ = _local_path(src, source.parent)
        if asset is None:
            raise ValueError(f"离线简报请使用已下载的本地图片：{src}")
        if not asset.is_file():
            raise FileNotFoundError(f"简报图片不存在：{asset}")
        mime = mimetypes.guess_type(asset.name)[0] or "application/octet-stream"
        image["src"] = f"data:{mime};base64," + base64.b64encode(asset.read_bytes()).decode("ascii")
        embedded_images += 1

    relocated_links = 0
    missing_links = []
    for link in soup.find_all("a", href=True):
        href = str(link["href"])
        if urlsplit(href).scheme.lower() in {"javascript", "vbscript", "data"}:
            raise ValueError(f"不支持的正文链接：{href}")
        asset, query, fragment = _local_path(href, source.parent)
        if asset is None:
            continue
        try:
            target = quote(Path(os.path.relpath(asset, output.parent)).as_posix(), safe="/")
        except ValueError:
            target = asset.as_uri()
        link["href"] = target + ("?" + query if query else "") + ("#" + fragment if fragment else "")
        relocated_links += 1
        if not asset.exists():
            missing_links.append(str(asset))

    used_ids = set()
    for heading in soup.find_all(re.compile(r"^h[1-6]$")):
        preferred = heading.get("id") or re.sub(r"[^\w\u4e00-\u9fff-]+", "-", heading.get_text(" ", strip=True)).strip("-") or "section"
        candidate, index = preferred, 2
        while candidate in used_ids or any(
            item is not heading and item.name not in {"h1", "h2", "h3", "h4", "h5", "h6"}
            for item in soup.find_all(id=candidate)
        ):
            candidate = f"{preferred}-{index}"
            index += 1
        heading["id"] = candidate
        used_ids.add(candidate)

    first_title = soup.find("h1")
    title = first_title.get_text(" ", strip=True) if first_title else source.stem
    headline = str(first_title.extract()) if first_title else f"<h1>{html.escape(title)}</h1>"
    toc = []
    for heading in soup.find_all(["h2", "h3"]):
        toc.append(
            f'<a href="#{quote(heading["id"], safe=""):s}" data-depth="{heading.name[1]}">'
            f'{html.escape(heading.get_text(" ", strip=True))}</a>'
        )
    for table in soup.find_all("table"):
        table["class"] = [*table.get("class", []), "result-table"]
        wrapper = soup.new_tag("div", attrs={"class": "table-scroll", "tabindex": "0", "role": "region", "aria-label": "数据表格，可左右滚动"})
        table.wrap(wrapper)

    template_path = Path(__file__).resolve().parents[1] / "assets" / "brief.html"
    template = template_path.read_text(encoding="utf-8")
    replacements = {
        "TITLE": html.escape(title),
        "HEADLINE": headline,
        "TOC": "\n".join(toc),
        "CONTENT": str(soup),
        "SOURCE_NAME": html.escape(source.name),
    }
    result = re.sub(r"\{\{(TITLE|HEADLINE|TOC|CONTENT|SOURCE_NAME)\}\}", lambda match: replacements[match.group(1)], template)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w" if force else "x", encoding="utf-8") as handle:
        handle.write(result)
    return {
        "output": str(output),
        "headings_in_directory": len(toc),
        "tables": len(soup.find_all("table")),
        "embedded_local_images": embedded_images,
        "relocated_attachment_links": relocated_links,
        "missing_local_links": missing_links,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="将分析简报 Markdown 制作为带左侧目录的离线 HTML。")
    parser.add_argument("source", help="输入 Markdown 文件")
    parser.add_argument("output", help="另存 HTML 文件")
    parser.add_argument("--force", action="store_true", help="明确覆盖已有 HTML，不改输入 Markdown")
    args = parser.parse_args()
    try:
        result = render(args.source, args.output, args.force)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(1, f"生成失败：{exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
