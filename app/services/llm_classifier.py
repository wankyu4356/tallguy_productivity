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

CLASSIFY_SYSTEM_PROMPT = f"""당신은 E&F 프라이빗에쿼티(PE)의 시각에서 한국 금융/투자 뉴스를 분류하는 전문가입니다.

다음 분류 체계에 따라 기사들을 분류하고 배치해주세요:

{CLASSIFICATION_TAXONOMY}

## 분류 규칙 (반드시 준수)

1. **IB하우스/증권사 관련 기사** → Deal > 기타
   - 주관 실적, 리그테이블, IB하우스 순위, 증권사 경쟁 등
   - 예: "삼성증권 IB 실적 1위", "IPO 주관 경쟁 심화"

2. **IPO/상장 관련 기사** → Deal > 투자회수
   - IPO, 상장 추진, 상장 예비심사, 공모, 스팩(SPAC) 합병 등은 PE의 관점에서 투자회수(Exit) 수단
   - "상장"이라는 단어가 포함되면 대부분 투자회수에 해당
   - 예: "○○기업 IPO 추진", "코스닥 상장 준비", "상장 추진 본격화", "상장 예비심사 청구", "공모주 시장", "스팩 합병 상장"

3. **CB(전환사채), BW(신주인수권부사채), 채권 발행** → Deal > 경영권 인수 및 매각, 투자 유치
   - 자금 조달/투자 유치의 일환
   - 예: "300억 CB 조달", "메자닌 투자", "BW 발행"

4. **PF(프로젝트 파이낸싱)** → Industry > E&F 포트폴리오 관련 산업 업계 동향 > 건설/부동산
   - PF는 건설/부동산 관련
   - 예: "부동산 PF 리스크", "PF 구조조정"

5. 위 4가지 규칙에 해당하지 않는 기사는, 기사 내용을 꼼꼼히 읽고 가장 적합한 카테고리에 분류하세요.
   분류 판단 시 논거를 탄탄하게 세워야 합니다:
   - 기사의 핵심 주제가 무엇인지
   - E&F PE 관점에서 어떤 카테고리와 가장 관련이 깊은지
   - 경계선 상의 기사는 PE 투자/운용 관점에서 더 의미 있는 쪽으로 배치

## 정렬 규칙 (매우 중요 — 반드시 숙고하여 정렬)

**각 하위 카테고리 내에서, 그리고 전체 article_order에서도, E&F PE의 시각에서 중요한 순으로 내림차순 배열하세요.**

### 중요도 판단 기준 (우선순위 순서):

**Tier 1 — 최우선 (맨 앞에 배치):**
- E&F PE가 직접 관여하거나 포트폴리오 기업과 직접 관련된 기사
- 1000억원 이상 대형 M&A, 바이아웃, 경영권 인수/매각
- PE 업계 전체에 파급력 있는 규제/정책 변화 (자본시장법, 사모펀드 규제 등)
- 대형 IPO (시가총액 5000억+ 또는 시장 주목도 높은 건)

**Tier 2 — 높은 중요도:**
- 중형 M&A (수백억원대), 주요 투자 유치/회수
- 주요 LP(국민연금, 공제회 등)의 출자/배분 관련
- GP 선정, 대형 블라인드펀드 결성
- E&F 포트폴리오 관련 산업(환경/폐기물, 건설/부동산, 바이오/헬스케어)의 핵심 트렌드

**Tier 3 — 보통 중요도:**
- 소형 딜 (수십억원대), 일반적 투자 유치
- 업종별 일반 동향 기사
- 증권사/IB 실적, 리그테이블
- CB/BW/메자닌 일반 발행

**Tier 4 — 낮은 중요도 (뒤쪽에 배치):**
- 개별 기업 실적 발표 (PE 관련성 낮은 경우)
- 인사이동, 조직개편
- 일반적 시장 코멘터리

### 같은 Tier 내 정렬:
- 거래 규모가 큰 것이 우선
- 시장 파급력이 큰 것이 우선
- PE/투자 관련성이 높은 것이 우선

한 기사는 하나의 카테고리에만 배치됩니다.

JSON 형식으로 응답해주세요. 반드시 classification_reasoning 필드에 각 기사의 분류 이유를 간단히 적어주세요.
또한 importance_tier 필드로 각 기사의 중요도 등급(1~4)을 반드시 명시해주세요."""


def _get_client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)


async def recommend_articles(
    articles: list[ArticleInfo],
    max_count: int | None = None,
) -> list[ArticleRecommendation]:
    """Use Claude to recommend which articles are worth including.

    Args:
        articles: List of articles to evaluate.
        max_count: If given, recommend approximately this many articles.
    """
    if not articles:
        return []

    client = _get_client()

    articles_text = "\n".join([
        f"[{a.id}] {a.title} (카테고리: {a.subcategory})\n  요약: {a.summary or '없음'}"
        for a in articles
    ])

    count_instruction = ""
    if max_count is not None:
        count_instruction = f"\n\n**중요: 전체 {len(articles)}개 기사 중 약 {max_count}개 내외로 추천해주세요. 가장 유의미한 기사를 우선적으로 선택하세요.**"

    prompt = f"""다음 기사 목록에서 PE 투자 전문가에게 유의미한 기사를 추천해주세요.{count_instruction}

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


async def classify_articles(
    articles: list[ArticleWithContent],
    strict: bool = False,
    previous_issues: str = "",
) -> ClassifiedOutput:
    """Use Claude to classify articles into the taxonomy.

    Args:
        articles: Articles with content to classify.
        strict: If True, use stricter prompt demanding thoroughness and
                validate that no major categories are empty. Used on retries.
        previous_issues: Description of issues from a previous classification
                         attempt (e.g. "투자회수 카테고리가 비어있었습니다").
    """
    if not articles:
        return ClassifiedOutput()

    client = _get_client()

    articles_text = "\n---\n".join([
        f"[{a.info.id}] 제목: {a.info.title}\n카테고리: {a.info.subcategory}\n내용: {a.content[:1500]}"
        for a in articles
    ])

    # Build strict mode additions
    strict_block = ""
    if strict:
        strict_block = """
## ⚠️ 재분류 모드 — 이전 분류가 부정확하여 다시 분류합니다. 더 엄밀하게 분류하고 정렬해주세요.

### 재분류 시 반드시 준수할 사항:

**[분류 정확성]**
1. **모든 하위 카테고리에 기사가 최소 1개 이상 배정되어야 합니다.** 비어있는 카테고리가 있으면 안 됩니다.
   - 특히 Deal > 투자회수, Deal > 기타, Fundraising 카테고리를 꼼꼼히 확인하세요.
   - IPO, 상장, 공모, 스팩(SPAC), 세컨더리, 투자 엑싯, 회수 관련 기사가 있다면 반드시 '투자회수'에 배정하세요.
2. **기사 내용을 처음부터 끝까지 꼼꼼히 다시 읽고**, 키워드뿐 아니라 기사의 핵심 맥락을 파악하여 분류하세요.
3. **경계선 상의 기사는 더 넓은 해석을 적용**하세요:
   - "상장" 언급 → 투자회수 우선 검토
   - "펀드" "출자" "LP" "GP" 언급 → Fundraising 우선 검토
   - "M&A" "인수" "매각" "투자" 언급 → Deal > 경영권 인수 및 매각, 투자 유치 우선 검토
4. **분류 이유(classification_reasoning)를 더 구체적으로** 작성하세요. 어떤 문장/키워드를 근거로 분류했는지 명시하세요.

**[정렬 정확성]**
5. **각 하위 카테고리 내 정렬을 처음부터 다시 검토하세요.** 이전 정렬이 부정확했을 수 있습니다.
   - 거래 규모, 시장 파급력, PE 관련성을 기준으로 하나하나 비교하여 순서를 결정하세요.
   - 단순히 기사 제목 순이나 크롤링 순서가 아닌, 실질적 중요도 순이어야 합니다.
6. **importance_tier를 반드시 명시하세요.** 각 기사에 Tier 1~4를 부여하고, 같은 카테고리 내에서 Tier가 낮은(더 중요한) 기사가 앞에 와야 합니다.
"""
        if previous_issues:
            strict_block += f"""
### 이전 분류의 문제점 (반드시 수정):
{previous_issues}
"""

    prompt = f"""다음 기사들을 분류 체계에 따라 분류하고, **중요도순으로 꼼꼼하게 정렬**해주세요.
{strict_block}
## 필수 분류 규칙 (반드시 적용):
1. IB하우스/증권사 관련(주관 실적, 리그테이블 등) → Deal > 기타
2. IPO 관련(상장, 공모, 코스닥/코스피 상장 등) → Deal > 투자회수
3. CB/BW/채권 발행/메자닌 → Deal > 경영권 인수 및 매각, 투자 유치
4. PF(프로젝트 파이낸싱) → Industry > E&F 포트폴리오 > 건설/부동산

## 중요도 정렬 (핵심 — 반드시 숙고):

**먼저 각 기사의 중요도 등급(Tier 1~4)을 판정한 후, 그에 따라 정렬하세요.**

- Tier 1: E&F PE 직접 관련, 대형 M&A(1000억+), 주요 규제 변화, 대형 IPO
- Tier 2: 중형 딜(수백억), 주요 LP 출자/배분, GP 선정, 대형 펀드 결성, 포트폴리오 산업 핵심 트렌드
- Tier 3: 소형 딜(수십억), 업종 일반 동향, IB 실적, CB/BW 일반 발행
- Tier 4: 개별 기업 실적(PE 무관), 인사이동, 일반 시장 코멘터리

**정렬 원칙:**
- 각 하위 카테고리(예: Deal > 경영권 인수, Deal > 투자회수 등) 안에서 Tier 1 → 2 → 3 → 4 순서
- 같은 Tier 안에서는 거래 규모 > 시장 파급력 > PE 관련성 순으로 정렬
- article_order도 전체적으로 분류 체계 순서(Deal → Industry → Fundraising)를 따르되 각 섹션 내 중요도순

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
  "article_order": ["기사ID1", "기사ID2", ...],
  "importance_tier": {{
    "기사ID": 1
  }},
  "classification_reasoning": {{
    "기사ID": "분류 이유 + 중요도 근거 (Tier X: ~이므로)"
  }}
}}

**중요**: 각 하위 카테고리의 기사 배열은 반드시 중요도 내림차순이어야 합니다.
article_order는 분류 체계 순서 + 각 섹션 내 중요도순입니다."""

    try:
        response = client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=8192,
            system=CLASSIFY_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        text = response.content[0].text
        json_match = _extract_json(text)
        if json_match:
            data = json.loads(json_match)

            # Log importance tiers
            tiers = data.get("importance_tier", {})
            if tiers:
                tier_summary = {}
                for aid, tier in tiers.items():
                    tier_summary.setdefault(tier, []).append(aid)
                for t in sorted(tier_summary.keys()):
                    logger.info(f"Tier {t}: {len(tier_summary[t])}개 기사")

            # Log classification reasoning for debugging
            reasoning = data.get("classification_reasoning", {})
            if reasoning:
                for aid, reason in reasoning.items():
                    tier_str = f"[Tier {tiers.get(aid, '?')}]" if tiers else ""
                    logger.info(f"Classification: [{aid}] {tier_str} → {reason}")

            result = _parse_classification(data, articles)

            # Post-process: re-sort each category's articles by importance tier
            if tiers:
                _resort_by_tier(result, tiers)

            # Validate: check for empty major categories
            empty_cats = _find_empty_categories(result)
            if empty_cats:
                logger.warning(f"빈 카테고리 발견: {', '.join(empty_cats)}")

                # On first pass (not strict), auto-retry once in strict mode
                if not strict:
                    logger.info("자동 재분류 시도 (strict mode)...")
                    issues = f"다음 카테고리가 비어있습니다: {', '.join(empty_cats)}. 기사를 다시 꼼꼼히 읽고 해당 카테고리에 배정할 기사가 있는지 확인하세요."
                    return await classify_articles(articles, strict=True, previous_issues=issues)

            return result
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


def _resort_by_tier(result: ClassifiedOutput, tiers: dict) -> None:
    """Re-sort article lists within each category by importance tier (ascending = more important first).

    This is a safety net: even if the LLM returned articles in the wrong order,
    we enforce Tier 1 before Tier 2 before Tier 3 etc.
    """
    def sort_key(aid: str) -> int:
        return int(tiers.get(aid, 9))  # unknown → last

    for cat in result.categories:
        if cat.articles:
            cat.articles.sort(key=sort_key)
        for sub in cat.subcategories:
            if sub.articles:
                sub.articles.sort(key=sort_key)
            for si in sub.sub_items:
                if si.articles:
                    si.articles.sort(key=sort_key)

    # Also re-sort article_order: group by category order (Deal→Industry→Fundraising),
    # then within each group sort by tier
    if result.article_order:
        # Build a map of article → category position
        cat_order = {}
        pos = 0
        for cat in result.categories:
            for aid in cat.articles:
                cat_order[aid] = pos
            for sub in cat.subcategories:
                for aid in sub.articles:
                    cat_order[aid] = pos
                for si in sub.sub_items:
                    for aid in si.articles:
                        cat_order[aid] = pos
            pos += 1

        result.article_order.sort(key=lambda aid: (cat_order.get(aid, 99), sort_key(aid)))


def _find_empty_categories(result: ClassifiedOutput) -> list[str]:
    """Find major subcategories that have zero articles assigned."""
    empty = []
    for cat in result.categories:
        if cat.subcategories:
            for sub in cat.subcategories:
                # Check sub-items (e.g., Industry > E&F 포트폴리오 > 환경/폐기물)
                if sub.sub_items:
                    # Parent sub has no direct articles and sub_items exist — check sub_items
                    pass
                else:
                    if not sub.articles:
                        empty.append(f"{cat.name} > {sub.name}")
        else:
            if not cat.articles:
                empty.append(cat.name)
    return empty


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
