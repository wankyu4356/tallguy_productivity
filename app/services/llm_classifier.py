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

## 분류 규칙 (반드시 준수 — 우선순위 순서대로 적용)

### 규칙 1: Deal > 경영권 인수 및 매각, 투자 유치
다음 키워드/주제가 기사의 **핵심 내용**인 경우:
- M&A, 인수, 매각, 경영권 거래, 지분 매각/인수
- 투자 유치, 자금 조달, CB(전환사채), BW(신주인수권부사채), 메자닌, 유상증자
- PE/VC의 신규 투자, 공동투자(co-invest), SPA 체결
- 구조조정, 워크아웃, 기업회생 (매각/투자 맥락)
- 키워드 예: "인수", "매각", "지분", "투자 유치", "CB 발행", "메자닌", "SPA", "LOI", "실사", "우선협상대상자", "본입찰", "예비입찰", "딜", "베인캐피탈", "MBK", "한앤컴퍼니", "IMM", "스틱", "어피너티", "글랜우드"

### 규칙 2: Deal > 투자회수
다음 키워드/주제가 기사의 **핵심 내용**인 경우:
- IPO, 상장, 공모, 상장예비심사, 코스닥/코스피 이전상장
- PE/VC의 투자금 회수, 엑시트(Exit), 세컨더리 매각, 블록딜
- 스팩(SPAC) 합병, 리캡(Recapitalization)
- 키워드 예: "상장", "IPO", "공모", "엑시트", "회수", "블록딜", "세컨더리", "오버행", "보호예수 해제", "구주매출"

### 규칙 3: Deal > 기타
다음 키워드/주제가 기사의 **핵심 내용**인 경우:
- IB하우스/증권사 관련: 주관 실적, 리그테이블, IB 순위, 증권사 딜 경쟁
- 위 두 규칙에 해당하지 않지만 딜/거래와 관련된 기타 주제
- 키워드 예: "IB 실적", "리그테이블", "주관 경쟁", "대표주관", "인수금융"

### 규칙 4: Industry > E&F 포트폴리오 관련 > 환경/폐기물
- 환경, 폐기물 처리, 재활용, 탄소배출, ESG 환경 관련 산업 동향
- 키워드 예: "폐기물", "환경", "재활용", "소각", "탄소", "그린", "환경부"

### 규칙 5: Industry > E&F 포트폴리오 관련 > 건설/부동산
- 건설, 부동산, PF(프로젝트파이낸싱), 개발사업, 시행사/시공사
- 키워드 예: "PF", "부동산", "건설", "분양", "재개발", "재건축", "시행사", "브릿지론"

### 규칙 6: Industry > E&F 포트폴리오 관련 > 바이오/헬스케어
- 바이오, 제약, 헬스케어, 의료기기, 디지털헬스 관련 산업 동향
- 키워드 예: "바이오", "제약", "헬스케어", "의료", "임상", "FDA", "신약", "CMO", "CDMO"

### 규칙 7: Industry > 기타 주요 산업 관련 업계 동향
- 위 3개 산업(환경, 건설, 바이오) 외의 산업 동향
- IT, 에너지, 유통, 식품, 제조업 등 기타 산업의 업계 동향
- **주의**: 특정 기업의 M&A/투자 유치 기사는 Industry가 아닌 Deal로 분류

### 규칙 8: Fundraising, LP 이슈 및 GP 선정
- 펀드 결성, 펀드레이징, LP 출자, GP 선정, 블라인드펀드, 프로젝트펀드
- 운용사(GP) 설립, 라이선스, 운용사 간 경쟁/이동
- 키워드 예: "펀드 결성", "출자", "LP", "GP 선정", "블라인드펀드", "커밋", "빈티지", "운용사", "DPI", "TVPI"

## 핵심 분류 원칙

1. **제목과 본문의 핵심 주제**를 기준으로 분류하세요. 단순히 키워드 하나만 보지 말고, 기사가 실제로 무엇에 대한 것인지를 파악하세요.
2. **"이 기사를 PE 투자자가 읽는다면, 어떤 섹션에서 찾기를 기대할까?"**를 기준으로 판단하세요.
3. 경계선 상의 기사는 PE 투자/운용 관점에서 더 핵심적인 쪽으로 배치하세요.
4. **한 기사는 반드시 하나의 카테고리에만** 배치합니다.

## 정렬 규칙

각 카테고리 내에서, 그리고 전체 article_order에서도, E&F PE의 중대성/중요성 관점에서
중요한 순으로 내림차순 배열하세요.

JSON 형식으로 응답해주세요. 반드시 classification_reasoning 필드에 각 기사의 분류 이유를 구체적으로 적어주세요."""


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
            max_tokens=8192,
            system=RECOMMEND_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        text = response.content[0].text
        # Extract JSON from response
        json_match = _extract_json(text)
        if json_match:
            data = json.loads(json_match)
            valid_ids = {a.id for a in articles}
            results = []
            seen_ids = set()
            for r in data.get("recommendations", []):
                aid = r.get("article_id", "")
                if aid in valid_ids:
                    results.append(ArticleRecommendation(**r))
                    seen_ids.add(aid)
            # Include articles the LLM missed (as not recommended)
            for a in articles:
                if a.id not in seen_ids:
                    results.append(ArticleRecommendation(
                        article_id=a.id, recommended=False, reason=""
                    ))
            return results
    except Exception as e:
        logger.error(f"LLM recommendation failed: {e}", exc_info=True)

    # Fallback: recommend all
    return [ArticleRecommendation(article_id=a.id, recommended=True, reason="자동 추천 실패 - 수동 선택 필요") for a in articles]


async def classify_articles(articles: list[ArticleWithContent]) -> ClassifiedOutput:
    """Use Claude to classify articles into the taxonomy."""
    if not articles:
        return ClassifiedOutput()

    client = _get_client()

    # Send more content for better classification accuracy
    articles_text = "\n---\n".join([
        f"[{a.info.id}] 제목: {a.info.title}\n원본 카테고리: {a.info.category} > {a.info.subcategory}\n요약: {a.info.summary or '없음'}\n본문:\n{a.content[:3000]}"
        for a in articles
    ])

    prompt = f"""다음 기사들을 분류 체계에 따라 분류하고, 중요도순으로 배치해주세요.

## 분류 절차 (반드시 이 순서대로 진행):

**Step 1**: 각 기사의 제목과 본문을 읽고, 기사의 핵심 주제를 파악하세요.
**Step 2**: 시스템 프롬프트의 분류 규칙 1~8을 순서대로 대조하여, 가장 적합한 카테고리를 결정하세요.
**Step 3**: classification_reasoning에 각 기사별로 "핵심 주제 → 적용 규칙 → 최종 분류"를 명시하세요.
**Step 4**: 각 카테고리 내에서 중요도순으로 정렬하세요.

## 주의사항:
- 기사의 **핵심 주제**가 무엇인지가 가장 중요합니다
- 단순히 키워드 하나에 의존하지 말고, 기사 전체 맥락을 고려하세요
- "이 기사를 PE 투자자가 읽는다면 어떤 섹션에서 찾겠는가?"를 기준으로 판단하세요

기사들:
{articles_text}

다음 JSON 형식으로 응답해주세요:
{{
  "classification_reasoning": {{
    "기사ID": "핵심 주제: ... → 적용 규칙: 규칙 N → 분류: 카테고리명"
  }},
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

**중요**: classification_reasoning을 classification보다 먼저 작성하세요. 분류 이유를 먼저 정리한 후에 최종 분류를 결정해야 정확도가 높아집니다.

article_order는 최종 PDF에 배치될 순서대로의 전체 기사 ID 목록입니다.
분류 체계 순서(Deal → Industry → Fundraising)를 따르되,
각 섹션 내에서는 중요도 내림차순으로 정렬하세요."""

    try:
        response = client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=16384,
            system=CLASSIFY_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        text = response.content[0].text
        json_match = _extract_json(text)
        if json_match:
            data = json.loads(json_match)
            # Log classification reasoning for debugging
            reasoning = data.get("classification_reasoning", {})
            if reasoning:
                for aid, reason in reasoning.items():
                    logger.info(f"Classification: [{aid}] → {reason}")
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
