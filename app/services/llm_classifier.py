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

CLASSIFICATION_TAXONOMY = """[딜사이트플러스]
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

CLASSIFY_SYSTEM_PROMPT = """당신은 E&F 프라이빗에쿼티(PE)의 시각에서 한국 금융/투자 뉴스를 분류하는 전문가입니다.
기사의 **제목과 본문 내용**을 꼼꼼히 읽고, 기사의 핵심 주제가 무엇인지 정확히 파악한 뒤 분류하세요.

⚠️ 중요: 기사에 표시된 "출처 섹션"은 딜사이트플러스 웹사이트의 원래 분류이며, 이것이 틀린 경우가 많습니다.
출처 섹션을 참고만 하되, 반드시 기사 본문 내용을 직접 읽고 판단하세요.

JSON 형식으로만 응답해주세요."""


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


async def classify_articles(articles: list[ArticleWithContent]) -> ClassifiedOutput:
    """Use Claude to classify articles into the taxonomy."""
    if not articles:
        return ClassifiedOutput()

    client = _get_client()

    articles_text = "\n---\n".join([
        f"[{a.info.id}] 제목: {a.info.title}\n출처 섹션(참고용): {a.info.subcategory}\n본문:\n{a.content[:3000]}"
        for a in articles
    ])

    prompt = f"""다음 기사들을 아래 분류 체계에 따라 분류해주세요.

{CLASSIFICATION_TAXONOMY}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## 분류 규칙 (우선순위 순서대로 적용)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 규칙 1: M&A, 인수, 매각, 투자 유치 → Deal > 경영권 인수 및 매각, 투자 유치 (acquisition_investment)
대상: 경영권 인수/매각, 지분 인수/매각, 투자 유치, SI(전략적 투자), FI(재무적 투자),
     CB(전환사채) 발행, BW(신주인수권부사채), 메자닌 투자, 유상증자, 자금조달,
     구조조정/워크아웃 중 매각/투자 관련, 인수전, 매물로 나온 기업, 실사(DD) 진행,
     SPA 체결, 우선협상대상자 선정, LOI 제출, 빅딜, 블록딜(지분 매각)
예시:
  ✅ "○○기업 인수전 3파전" → acquisition_investment
  ✅ "PE, ○○지분 매각 추진" → acquisition_investment
  ✅ "300억 CB 조달" → acquisition_investment (자금조달/투자유치)
  ✅ "○○, 시리즈B 500억 투자유치" → acquisition_investment
  ✅ "○○ 매각 본입찰 실시" → acquisition_investment
  ✅ "블록딜로 지분 정리" → acquisition_investment (지분 매각)

### 규칙 2: IPO, 상장, 투자회수(Exit) → Deal > 투자회수 (exit)
대상: IPO 추진/준비, 상장 예비심사, 코스닥/코스피 상장, 공모, 스팩(SPAC) 합병,
     PE의 투자금 회수, 세컨더리 매각(투자자 간 지분 거래로 Exit하는 경우),
     구주매출, 오버행(상장 후 지분 매각 이슈), 보호예수 해제
예시:
  ✅ "○○기업 IPO 추진" → exit
  ✅ "코스닥 상장 예비심사 청구" → exit
  ✅ "PE, 상장으로 투자금 회수" → exit
  ✅ "스팩 합병 상장 추진" → exit
  ✅ "보호예수 해제 물량 출회" → exit

### 규칙 3: IB/증권사 실적, 리그테이블, 주관 경쟁 → Deal > 기타 (etc)
대상: IB하우스 순위, 주관 실적, 리그테이블, 증권사 IB 부문 실적/경쟁,
     ECM/DCM 시장 동향(특정 딜이 아닌 시장 전체 흐름), 자문사 선임(특정 딜보다 IB 업계 관점이 중심인 경우)
예시:
  ✅ "삼성증권 IB 실적 1위" → etc (Deal > 기타)
  ✅ "올해 ECM 주관 경쟁 심화" → etc (Deal > 기타)
  ✅ "IPO 주관사 순위 변동" → etc (Deal > 기타)
  ⚠️ 단, 특정 기업의 IPO 자체 기사("○○기업 IPO 추진")는 exit로 분류

### 규칙 4: 환경/폐기물 산업 → Industry > E&F 포트폴리오 > 환경/폐기물 (environment_waste)
대상: 폐기물 처리, 환경 규제, 탄소배출권, ESG(환경 관련), 재활용, 소각장,
     환경오염, 매립지, 폐기물 업체 동향
예시:
  ✅ "폐기물 처리업체 인허가 강화" → environment_waste
  ✅ "탄소배출권 거래 시장 동향" → environment_waste

### 규칙 5: 건설/부동산/PF → Industry > E&F 포트폴리오 > 건설/부동산 (construction_realestate)
대상: 건설사 동향, 부동산 시장, PF(프로젝트 파이낸싱), 분양, 재건축/재개발,
     SOC(사회간접자본), 인프라 투자, 건설 수주, 부동산 개발
예시:
  ✅ "부동산 PF 리스크 확대" → construction_realestate
  ✅ "○○건설 수주 실적" → construction_realestate
  ✅ "재건축 규제 완화" → construction_realestate

### 규칙 6: 바이오/헬스케어/제약 → Industry > E&F 포트폴리오 > 바이오/헬스케어 (bio_healthcare)
대상: 제약사, 바이오텍, 헬스케어, 의료기기, 신약 개발, 기술이전(L/O),
     임상시험, FDA/식약처 승인, 병원/의료 서비스
예시:
  ✅ "○○바이오 신약 임상 3상 진입" → bio_healthcare
  ✅ "제약사 기술이전 계약" → bio_healthcare

### 규칙 7: 기타 산업 동향 → Industry > 기타 주요 산업 관련 업계 동향 (industry_etc)
대상: 위 3개 포트폴리오 산업(환경, 건설, 바이오)에 해당하지 않는 산업 동향
     (IT, 유통, 에너지, 식품, 자동차, 금융 등 기타 산업의 업계 동향)
예시:
  ✅ "반도체 업황 회복세" → industry_etc
  ✅ "유통업계 구조조정" → industry_etc

### 규칙 8: 펀드레이징/LP/GP → Fundraising, LP 이슈 및 GP 선정 (fundraising)
대상: 블라인드펀드 결성, 프로젝트펀드, LP(유한책임사원) 출자, GP(무한책임사원) 선정,
     국민연금/공제회 등 기관투자자 출자, 펀드 클로징, 앵커 LP 확보,
     GP 탈락/선정, 운용사 설립/인가
예시:
  ✅ "○○PE 1조원 블라인드펀드 결성" → fundraising
  ✅ "국민연금 PE 출자 확대" → fundraising
  ✅ "신규 GP 운용사 설립 러시" → fundraising

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## 주의사항 (자주 틀리는 케이스)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. **"출처 섹션"을 맹신하지 마세요.** 출처가 "Deals - ECM"이어도 본문이 바이오 산업 동향이면 bio_healthcare입니다.
2. **"Investors - PEF/VC" 섹션 기사**를 무조건 한 곳에 넣지 마세요:
   - PE가 특정 기업을 인수/매각하는 내용 → acquisition_investment
   - PE의 투자 회수/Exit 내용 → exit
   - PE 펀드 결성/LP 출자 내용 → fundraising
   - PE 업계 전반 동향 → Deal > 기타
3. **"Investors - IB" 섹션 기사**도 마찬가지:
   - IB 업계 전체 동향/순위 → Deal > 기타
   - 특정 딜의 자문/주관 내용이 핵심이면 → 해당 딜의 성격에 따라 분류
4. **제목에 "인수"가 있어도** 본문을 읽어보면 산업 동향 기사일 수 있습니다. 본문 확인 필수.
5. **"Deals - PF" 섹션** → 대부분 construction_realestate가 맞지만, PF 관련 금융상품/구조화 기사는 Deal > 기타일 수 있습니다.
6. **블록딜**: 투자자가 보유 지분을 장내에서 대량 매각하는 것.
   - PE/VC가 Exit 목적이면 → exit
   - 대주주의 지분 정리/경영권 변동이면 → acquisition_investment

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## 정렬 규칙
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

각 카테고리 내 + article_order 전체에서, E&F PE 중요도 내림차순.
- E&F 포트폴리오 직접 관련 > 대형 딜 > 소형 딜 > 일반 동향
- 한 기사는 하나의 카테고리에만 배치

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

기사들:
{articles_text}

다음 JSON 형식으로 응답해주세요 (JSON만 출력, 다른 텍스트 없이):
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
  "classification_reasoning": {{
    "기사ID": "적용규칙 번호 + 핵심 근거 한 줄"
  }}
}}

article_order는 Deal → Industry → Fundraising 순서로, 각 섹션 내 중요도 내림차순."""

    try:
        response = client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=8192,
            system=CLASSIFY_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        text = response.content[0].text
        logger.info(f"LLM classification response length: {len(text)} chars")
        json_match = _extract_json(text)
        if json_match:
            data = json.loads(json_match)
            # Log classification reasoning for debugging
            reasoning = data.get("classification_reasoning", {})
            if reasoning:
                for aid, reason in reasoning.items():
                    logger.info(f"Classification: [{aid}] → {reason}")
            result = _parse_classification(data, articles)
            # 결과 검증
            total_classified = sum(
                len(cat.articles) + sum(
                    len(sub.articles) + sum(len(si.articles) for si in sub.sub_items)
                    for sub in cat.subcategories
                ) for cat in result.categories
            )
            logger.info(f"Classification result: {total_classified} articles classified into categories")
            if total_classified == 0:
                logger.error(f"Classification returned 0 articles! LLM response (first 500): {text[:500]}")
            return result
        else:
            logger.error(f"Failed to extract JSON from LLM response. Response (first 500): {text[:500]}")
    except Exception as e:
        logger.error(f"LLM classification failed: {e}", exc_info=True)

    # Fallback: 모든 기사를 Deal > 기타에 배치
    all_ids = [a.info.id for a in articles]
    logger.warning(f"Using fallback classification for {len(all_ids)} articles")
    return ClassifiedOutput(
        article_order=all_ids,
        categories=[
            ClassificationCategory(
                name="Deal",
                subcategories=[
                    ClassificationSubcategory(name="경영권 인수 및 매각, 투자 유치"),
                    ClassificationSubcategory(name="투자회수"),
                    ClassificationSubcategory(
                        name="기타",
                        articles=all_ids,  # 전부 여기에 배치
                    ),
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

    # LLM이 반환한 ID를 실제 ID와 매칭 (괄호, 공백, 따옴표 제거 등 정규화)
    valid_ids = {a.info.id for a in articles}
    # 숫자만 추출한 역매핑 테이블
    import re as _re
    id_by_digits = {}
    for a in articles:
        digits = _re.sub(r'[^0-9a-fA-F]', '', a.info.id)
        if digits:
            id_by_digits[digits] = a.info.id

    def normalize_ids(raw_ids):
        """LLM이 반환한 ID 리스트를 정규화하여 실제 ID와 매칭."""
        if not isinstance(raw_ids, list):
            return []
        result = []
        for rid in raw_ids:
            rid_str = str(rid).strip().strip('"').strip("'")
            # 1) 그대로 매칭
            if rid_str in valid_ids:
                result.append(rid_str)
                continue
            # 2) 괄호 제거 [123] → 123
            cleaned = rid_str.strip('[]').strip()
            if cleaned in valid_ids:
                result.append(cleaned)
                continue
            # 3) 숫자/hex만 추출해서 매칭
            digits = _re.sub(r'[^0-9a-fA-F]', '', rid_str)
            if digits and digits in id_by_digits:
                result.append(id_by_digits[digits])
                continue
            # 4) 부분 문자열 매칭 (ID가 다른 문자열에 포함되어 있는 경우)
            found = False
            for vid in valid_ids:
                if vid in rid_str or rid_str in vid:
                    result.append(vid)
                    found = True
                    break
            if not found:
                logger.warning(f"Classification: unmatched article ID '{rid_str}' (original: '{rid}')")
        return result

    deal = cls.get("deal", {})
    industry = cls.get("industry", {})
    fundraising = cls.get("fundraising", [])

    categories = [
        ClassificationCategory(
            name="Deal",
            subcategories=[
                ClassificationSubcategory(
                    name="경영권 인수 및 매각, 투자 유치",
                    articles=normalize_ids(deal.get("acquisition_investment", [])),
                ),
                ClassificationSubcategory(
                    name="투자회수",
                    articles=normalize_ids(deal.get("exit", [])),
                ),
                ClassificationSubcategory(
                    name="기타",
                    articles=normalize_ids(deal.get("etc", [])),
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
                            articles=normalize_ids(industry.get("environment_waste", [])),
                        ),
                        ClassificationSubItem(
                            name="건설/부동산",
                            articles=normalize_ids(industry.get("construction_realestate", [])),
                        ),
                        ClassificationSubItem(
                            name="바이오/헬스케어",
                            articles=normalize_ids(industry.get("bio_healthcare", [])),
                        ),
                    ],
                ),
                ClassificationSubcategory(
                    name="기타 주요 산업 관련 업계 동향",
                    articles=normalize_ids(industry.get("etc", [])),
                ),
            ],
        ),
        ClassificationCategory(
            name="Fundraising, LP 이슈 및 GP 선정",
            articles=normalize_ids(fundraising if isinstance(fundraising, list) else []),
        ),
    ]

    # Ensure all article IDs are in article_order
    all_ids = {a.info.id for a in articles}
    ordered_ids = normalize_ids(article_order)
    missing = all_ids - set(ordered_ids)
    ordered_ids.extend(missing)

    # 분류에 포함되지 않은 기사는 첫 번째 카테고리에 추가
    classified_ids = set()
    for cat in categories:
        classified_ids.update(cat.articles)
        for sub in cat.subcategories:
            classified_ids.update(sub.articles)
            for si in sub.sub_items:
                classified_ids.update(si.articles)

    unclassified = all_ids - classified_ids
    if unclassified:
        logger.warning(f"Classification: {len(unclassified)} articles not classified, adding to Deal > 기타")
        # Deal > 기타 subcategory에 추가
        if categories and categories[0].subcategories:
            etc_sub = categories[0].subcategories[-1]  # "기타"
            etc_sub.articles.extend(list(unclassified))

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
