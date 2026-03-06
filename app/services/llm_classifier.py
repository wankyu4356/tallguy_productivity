import json
import anthropic

from app.config import settings
from app.models.schemas import (
    ArticleInfo, ArticleRecommendation, ArticleWithContent,
    ClassifiedOutput, ClassificationCategory, ClassificationSubcategory,
    ClassificationSubItem,
)
from app.utils.logging import get_logger

logger = get_logger(__name__)

CLASSIFICATION_TAXONOMY = """[더벨]
1. Deal
   A. 경영권 인수 및 매각, 투자 유치
   B. 투자회수
   C. 기타
2. Industry
   A. E&F 포트폴리오 관련 산업 업계 동향
      - 환경/폐기물
      - 건설/부동산
      - 바이오/헬스케어
   B. 기타 주요 산업 관련 업계 동향
3. Fundraising, LP 이슈 및 GP 선정"""

RECOMMEND_SYSTEM_PROMPT = """당신은 한국 금융/투자 업계의 뉴스 분석 전문가입니다.
사모펀드(PE) 투자 전문가의 시각에서 기사의 유의미성을 판단합니다.

다음 기준으로 기사를 추천해주세요:
- Deal 관련: M&A, 투자 유치, 투자회수, IPO, 구조조정 등 거래 관련
- Industry: PE 포트폴리오와 관련된 산업 동향 (환경/폐기물, 건설/부동산, 바이오/헬스케어)
- Fundraising: 펀드레이징, LP 이슈, GP 선정 관련
- 일반적인 시장 동향이나 개별 기업 실적 기사는 제외

JSON 형식으로 응답해주세요."""

CLASSIFY_SYSTEM_PROMPT = f"""당신은 한국 금융/투자 뉴스를 분류하는 전문가입니다.

다음 분류 체계에 따라 기사들을 분류하고 배치해주세요:

{CLASSIFICATION_TAXONOMY}

각 기사를 가장 적합한 카테고리에 배치하세요.
한 기사는 하나의 카테고리에만 배치됩니다.
같은 카테고리 내에서는 중요도순으로 정렬하세요.

JSON 형식으로 응답해주세요."""


def _get_client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)


async def recommend_articles(articles: list[ArticleInfo]) -> list[ArticleRecommendation]:
    """Use Claude to recommend which articles are worth including."""
    if not articles:
        return []

    client = _get_client()

    articles_text = "\n".join([
        f"[{a.id}] {a.title} (카테고리: {a.subcategory})\n  요약: {a.summary or '없음'}"
        for a in articles
    ])

    prompt = f"""다음 기사 목록에서 PE 투자 전문가에게 유의미한 기사를 추천해주세요.

기사 목록:
{articles_text}

다음 JSON 형식으로 응답해주세요:
{{
  "recommendations": [
    {{
      "article_id": "기사ID",
      "recommended": true/false,
      "reason": "추천/비추천 이유 (간단히)"
    }}
  ]
}}"""

    try:
        response = client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=4096,
            system=RECOMMEND_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        text = response.content[0].text
        # Extract JSON from response
        json_match = _extract_json(text)
        if json_match:
            data = json.loads(json_match)
            return [
                ArticleRecommendation(**r)
                for r in data.get("recommendations", [])
            ]
    except Exception as e:
        logger.error(f"LLM recommendation failed: {e}", exc_info=True)

    # Fallback: recommend all
    return [ArticleRecommendation(article_id=a.id, recommended=True, reason="자동 추천 실패 - 수동 선택 필요") for a in articles]


async def classify_articles(articles: list[ArticleWithContent]) -> ClassifiedOutput:
    """Use Claude to classify articles into the taxonomy."""
    if not articles:
        return ClassifiedOutput()

    client = _get_client()

    articles_text = "\n---\n".join([
        f"[{a.info.id}] 제목: {a.info.title}\n카테고리: {a.info.subcategory}\n내용: {a.content[:1000]}"
        for a in articles
    ])

    prompt = f"""다음 기사들을 분류 체계에 따라 분류하고, 각 카테고리 내에서 중요도순으로 배치해주세요.

기사들:
{articles_text}

다음 JSON 형식으로 응답해주세요:
{{
  "classification": {{
    "deal": {{
      "acquisition_investment": ["기사ID", ...],
      "exit": ["기사ID", ...],
      "etc": ["기사ID", ...]
    }},
    "industry": {{
      "environment_waste": ["기사ID", ...],
      "construction_realestate": ["기사ID", ...],
      "bio_healthcare": ["기사ID", ...],
      "etc": ["기사ID", ...]
    }},
    "fundraising": ["기사ID", ...]
  }},
  "article_order": ["기사ID1", "기사ID2", ...]
}}

article_order는 최종 PDF에 배치될 순서대로의 전체 기사 ID 목록입니다.
분류 체계 순서(Deal → Industry → Fundraising)를 따르세요."""

    try:
        response = client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=4096,
            system=CLASSIFY_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        text = response.content[0].text
        json_match = _extract_json(text)
        if json_match:
            data = json.loads(json_match)
            return _parse_classification(data, articles)
    except Exception as e:
        logger.error(f"LLM classification failed: {e}", exc_info=True)

    # Fallback: put all in order as-is
    return ClassifiedOutput(
        article_order=[a.info.id for a in articles],
        categories=[
            ClassificationCategory(
                name="Deal",
                subcategories=[
                    ClassificationSubcategory(name="경영권 인수 및 매각, 투자 유치"),
                    ClassificationSubcategory(name="투자회수"),
                    ClassificationSubcategory(name="기타"),
                ],
            ),
            ClassificationCategory(
                name="Industry",
                subcategories=[
                    ClassificationSubcategory(
                        name="E&F 포트폴리오 관련 산업 업계 동향",
                        sub_items=[
                            ClassificationSubItem(name="환경/폐기물"),
                            ClassificationSubItem(name="건설/부동산"),
                            ClassificationSubItem(name="바이오/헬스케어"),
                        ],
                    ),
                    ClassificationSubcategory(name="기타 주요 산업 관련 업계 동향"),
                ],
            ),
            ClassificationCategory(name="Fundraising, LP 이슈 및 GP 선정"),
        ],
    )


def _parse_classification(data: dict, articles: list[ArticleWithContent]) -> ClassifiedOutput:
    """Parse the LLM classification response into structured output."""
    cls = data.get("classification", {})
    article_order = data.get("article_order", [])

    deal = cls.get("deal", {})
    industry = cls.get("industry", {})
    fundraising = cls.get("fundraising", [])

    categories = [
        ClassificationCategory(
            name="Deal",
            subcategories=[
                ClassificationSubcategory(
                    name="경영권 인수 및 매각, 투자 유치",
                    articles=deal.get("acquisition_investment", []),
                ),
                ClassificationSubcategory(
                    name="투자회수",
                    articles=deal.get("exit", []),
                ),
                ClassificationSubcategory(
                    name="기타",
                    articles=deal.get("etc", []),
                ),
            ],
        ),
        ClassificationCategory(
            name="Industry",
            subcategories=[
                ClassificationSubcategory(
                    name="E&F 포트폴리오 관련 산업 업계 동향",
                    sub_items=[
                        ClassificationSubItem(
                            name="환경/폐기물",
                            articles=industry.get("environment_waste", []),
                        ),
                        ClassificationSubItem(
                            name="건설/부동산",
                            articles=industry.get("construction_realestate", []),
                        ),
                        ClassificationSubItem(
                            name="바이오/헬스케어",
                            articles=industry.get("bio_healthcare", []),
                        ),
                    ],
                ),
                ClassificationSubcategory(
                    name="기타 주요 산업 관련 업계 동향",
                    articles=industry.get("etc", []),
                ),
            ],
        ),
        ClassificationCategory(
            name="Fundraising, LP 이슈 및 GP 선정",
            articles=fundraising if isinstance(fundraising, list) else [],
        ),
    ]

    # Ensure all article IDs are in article_order
    all_ids = {a.info.id for a in articles}
    ordered_ids = [aid for aid in article_order if aid in all_ids]
    missing = all_ids - set(ordered_ids)
    ordered_ids.extend(missing)

    return ClassifiedOutput(categories=categories, article_order=ordered_ids)


def _extract_json(text: str) -> str | None:
    """Extract JSON from LLM response text."""
    # Try to find JSON block
    import re
    # Look for ```json ... ``` blocks
    match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if match:
        return match.group(1).strip()
    # Try to find raw JSON object
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        return match.group(0)
    return None
