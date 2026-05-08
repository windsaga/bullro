"""posts/ 폴더의 .md 파일을 WordPress에 강제 발행.

사용법:
    # 특정 파일
    python scripts/force_publish.py posts/2026-05-08-some-post.md

    # posts/ 전체 (발행 안 된 것만)
    python scripts/force_publish.py --all

    # posts/ 전체 (이미 발행된 것도 재발행)
    python scripts/force_publish.py --all --force
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from pathlib import Path

# 프로젝트 루트를 path에 추가
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# .env 로드 (pipeline.config 임포트 전에 실행해야 함)
_env_path = ROOT / ".env"
if _env_path.exists():
    for _line in _env_path.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

# force_publish는 NVIDIA API 불필요 — 미설정 시 더미값으로 config 통과
os.environ.setdefault("NVIDIA_API_KEY", "dummy-not-used")

from pipeline.config import cfg
from pipeline.publisher import publish_to_wordpress, PublishResult
from pipeline.models import Post, Draft, SEOMeta, FactCheckResult, SynthesizedFacts, SelectedTopic, Article

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """YAML frontmatter 파싱. (dict, 본문) 반환."""
    text = text.strip()
    if not text.startswith("---"):
        return {}, text
    end = text.find("---", 3)
    if end == -1:
        return {}, text

    fm_text = text[3:end].strip()
    body = text[end + 3:].lstrip()

    meta: dict = {}
    for line in fm_text.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()

    return meta, body


def _build_post_from_md(md_path: Path) -> Post:
    """Markdown 파일 → Post 객체."""
    raw = md_path.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(raw)

    title = meta.get("title", md_path.stem)
    slug = meta.get("slug", re.sub(r"[^a-z0-9-]", "", title.lower().replace(" ", "-"))[:60])
    thumbnail_url = meta.get("thumbnail", "") or ""

    # tags: "['AI', 'LLM']" 형태 파싱
    tags_raw = meta.get("tags", "")
    tags: list[str] = []
    if tags_raw:
        tags = [t.strip().strip("'\"[]") for t in tags_raw.split(",") if t.strip().strip("'\"[] ")]

    meta_description = meta.get("meta_description", "")

    # 최소한의 더미 객체로 Post 조립
    dummy_article = Article(url=meta.get("source_url", ""), title=title, content="", source="manual")
    dummy_topic = SelectedTopic(article=dummy_article, angle=meta.get("angle", ""), rationale="", score=0)
    dummy_facts = SynthesizedFacts(topic=dummy_topic, claims=[], data_points=[], mechanism="", limitations=[])
    draft = Draft(facts=dummy_facts, content=body, model="manual", version=1)
    seo = SEOMeta(
        title_candidates=[title],
        meta_description=meta_description,
        tags=tags,
        thumbnail_prompt="",
        internal_link_slots=[],
    )
    fact_check = FactCheckResult(claims=[], unsupported_count=0)

    return Post(
        draft=draft,
        fact_check=fact_check,
        seo=seo,
        thumbnail_url=thumbnail_url,
        chosen_title=title,
        slug=slug,
        status="ready",
    )


def publish_file(md_path: Path) -> bool:
    log.info(f"발행 시도: {md_path.name}")
    try:
        post = _build_post_from_md(md_path)
        result: PublishResult = publish_to_wordpress(post)
        log.info(f"완료: {result.link}  (wp_id={result.wp_id})")
        return True
    except Exception as e:
        log.error(f"실패 [{md_path.name}]: {e}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="posts/ 폴더 강제 발행")
    parser.add_argument("files", nargs="*", help="발행할 .md 파일 경로")
    parser.add_argument("--all", action="store_true", help="posts/ 폴더 전체")
    parser.add_argument("--force", action="store_true", help="이미 발행된 파일도 재발행")
    args = parser.parse_args()

    if args.all:
        targets = sorted(cfg.POSTS_DIR.glob("*.md"))
        if not args.force:
            # low_quality / pending 제외
            targets = [p for p in targets if "low_quality" not in p.name and "pending" not in p.name]
        log.info(f"대상 파일 {len(targets)}개")
    elif args.files:
        targets = [Path(f) for f in args.files]
    else:
        parser.print_help()
        sys.exit(1)

    ok, fail = 0, 0
    for path in targets:
        if not path.exists():
            log.warning(f"파일 없음: {path}")
            fail += 1
            continue
        if publish_file(path):
            ok += 1
        else:
            fail += 1

    log.info(f"결과: 성공 {ok}건 / 실패 {fail}건")


if __name__ == "__main__":
    main()
