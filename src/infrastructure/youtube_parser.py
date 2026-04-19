from __future__ import annotations

from src.core.entities import RawTrendData


def score_from_rank(rank: int, total: int) -> int:
    if total <= 1:
        return 100
    return max(1, round(100 * (1 - rank / total)))


def extract_text(obj: object) -> str:
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj.strip()
    if isinstance(obj, dict):
        if "simpleText" in obj:
            return str(obj["simpleText"]).strip()
        if "runs" in obj:
            runs: list[dict[str, str]] = obj["runs"]
            return "".join(r.get("text", "") for r in runs).strip()
    return ""


def parse_view_count(text: str) -> int:
    if not text:
        return 0
    t = text.lower().replace("views", "").replace("watching", "").strip()
    multipliers = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}
    for suffix, mult in multipliers.items():
        if t.endswith(suffix):
            try:
                return int(float(t[:-1].replace(",", "")) * mult)
            except ValueError:
                return 0
    try:
        return int(t.replace(",", "").split(".")[0])
    except ValueError:
        return 0


def video_renderers_to_records(
    renderers: list[dict[str, object]],
    region: str,
    source_name: str,
) -> list[RawTrendData]:
    """Convert raw videoRenderer dicts → RawTrendData entities."""
    total = len(renderers)
    records: list[RawTrendData] = []

    for rank, vr in enumerate(renderers):
        title = extract_text(vr.get("title"))
        if not title:
            continue

        video_id = str(vr.get("videoId", ""))
        channel = extract_text(vr.get("longBylineText") or vr.get("ownerText"))
        view_count_text = extract_text(
            vr.get("viewCountText") or vr.get("shortViewCountText")
        )
        view_count = parse_view_count(view_count_text)

        records.append(
            RawTrendData(
                keyword=title,
                region=region,
                raw_value=score_from_rank(rank, total),
                source=source_name,
                metadata={
                    "video_id": video_id,
                    "channel": channel,
                    "view_count_text": view_count_text,
                    "view_count_approx": view_count,
                    "rank": rank,
                    "total_results": total,
                    "endpoint": "innertube",
                },
            )
        )

    return records


def extract_flat_video_renderers(
    data: dict[str, object],
) -> list[dict[str, object]]:
    """Walk the full Innertube JSON tree and collect any videoRenderer nodes."""
    results: list[dict[str, object]] = []

    def _walk(node: object) -> None:
        if isinstance(node, dict):
            if "videoId" in node and "title" in node:
                results.append(node)  # type: ignore[arg-type]
            for v in node.values():
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(data)
    return results


def parse_innertube_response(
    data: dict[str, object],
    region: str,
    source_name: str,
) -> list[RawTrendData]:
    """Parse structured Innertube browse response into RawTrendData records."""
    video_renderers: list[dict[str, object]] = []

    tabs: list[dict[str, object]] = (
        data.get("contents", {})
        .get("twoColumnBrowseResultsRenderer", {})
        .get("tabs", [])
    )
    for tab in tabs:
        tab_content = (
            tab.get("tabRenderer", {})
            .get("content", {})
            .get("sectionListRenderer", {})
            .get("contents", [])
        )
        for section in tab_content:
            items = section.get("itemSectionRenderer", {}).get("contents", [])
            for item in items:
                shelf = item.get("shelfRenderer", {})
                shelf_items = (
                    shelf.get("content", {})
                    .get("expandedShelfContentsRenderer", {})
                    .get("items", [])
                )
                for shelf_item in shelf_items:
                    vr = shelf_item.get("videoRenderer")
                    if vr:
                        video_renderers.append(vr)

    if not video_renderers:
        video_renderers = extract_flat_video_renderers(data)

    if not video_renderers:
        return []

    return video_renderers_to_records(video_renderers, region, source_name)