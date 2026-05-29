import json
import time
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
기사의 **제목과 본문 내용**을 꼼꼼히 읽고, 기사의 핵심 주제가 무엇인지 정확히 파악한 뒤 분류하세요.

⚠️ 중요: 기사에 표시된 "출처 섹션"은 더벨 웹사이트의 원래 분류이며, 이것이 틀린 경우가 많습니다.
출처 섹션을 참고만 하되, 반드시 기사 본문 내용을 직접 읽고 판단하세요.

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

    content_limit = 4000 if strict else 1500
    articles_text = "\n---\n".join([
        f"[{a.info.id}] 제목: {a.info.title}\n출처 섹션(참고용): {a.info.subcategory}\n본문:\n{a.content[:content_limit]}"
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
{strict_block}
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
  "importance_tier": {{
    "기사ID": 1
  }},
  "classification_reasoning": {{
    "기사ID": "적용규칙 번호 + 핵심 근거 한 줄"
  }}
}}

article_order는 Deal → Industry → Fundraising 순서로, 각 섹션 내 중요도 내림차순."""

    # API key validation
    if not settings.ANTHROPIC_API_KEY:
        logger.error("ANTHROPIC_API_KEY가 설정되지 않았습니다! .env 파일을 확인하세요.")
        return _fallback_classification(articles, reason="API 키 미설정")

    # Retry loop: max 3 attempts with exponential backoff
    last_error = None
    for attempt in range(3):
        try:
            if attempt > 0:
                wait = 2 ** attempt
                logger.info(f"Classification retry {attempt}/2, waiting {wait}s...")
                time.sleep(wait)

            create_kwargs = dict(
                model=settings.CLAUDE_MODEL,
                max_tokens=20000 if strict else 16384,
                system=CLASSIFY_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            if strict:
                create_kwargs["thinking"] = {"type": "enabled", "budget_tokens": 8000}
                logger.info("재분류: extended thinking 활성화 (budget=8000)")

            try:
                response = client.messages.create(**create_kwargs)
            except (TypeError, anthropic.BadRequestError) as e:
                logger.warning(f"extended thinking 미지원 — 일반 호출로 폴백: {e}")
                create_kwargs.pop("thinking", None)
                create_kwargs["max_tokens"] = 16384
                response = client.messages.create(**create_kwargs)

            text = next(
                (b.text for b in response.content if getattr(b, "type", None) == "text"),
                "",
            )
            stop_reason = response.stop_reason
            logger.info(
                f"LLM classification response: {len(text)} chars, "
                f"stop_reason={stop_reason}, model={settings.CLAUDE_MODEL}"
            )

            # Detect truncation
            if stop_reason == "max_tokens":
                logger.warning("Classification response truncated (max_tokens reached)! Retrying...")
                last_error = "Response truncated (max_tokens)"
                continue

            json_match = _extract_json(text)
            if not json_match:
                logger.error(
                    f"Failed to extract JSON from LLM response. "
                    f"Response (first 1000): {text[:1000]}"
                )
                last_error = "JSON extraction failed"
                continue

            data = json.loads(json_match)

            # Log importance tiers
            tiers = data.get("importance_tier", {})
            if tiers:
                tier_summary: dict[int, list[str]] = {}
                for aid, tier in tiers.items():
                    tier_summary.setdefault(tier, []).append(aid)
                for t in sorted(tier_summary.keys()):
                    logger.info(f"Tier {t}: {len(tier_summary[t])}개 기사")

            # Log classification reasoning (first 5 only)
            reasoning = data.get("classification_reasoning", {})
            if reasoning:
                for aid, reason in list(reasoning.items())[:5]:
                    logger.info(f"Classification: [{aid}] → {reason}")
                if len(reasoning) > 5:
                    logger.info(f"  ... and {len(reasoning) - 5} more articles classified")

            result = _parse_classification(data, articles)

            # Validate total classified count
            total_classified = sum(
                len(cat.articles) + sum(
                    len(sub.articles) + sum(len(si.articles) for si in sub.sub_items)
                    for sub in cat.subcategories
                )
                for cat in result.categories
            )
            logger.info(f"Classification result: {total_classified} articles classified into categories")
            if total_classified == 0:
                logger.error(
                    f"Classification returned 0 articles! "
                    f"LLM response (first 500): {text[:500]}"
                )
                last_error = "0 articles classified from response"
                continue

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
                    issues = (
                        f"다음 카테고리가 비어있습니다: {', '.join(empty_cats)}. "
                        f"기사를 다시 꼼꼼히 읽고 해당 카테고리에 배정할 기사가 있는지 확인하세요."
                    )
                    return await classify_articles(articles, strict=True, previous_issues=issues)

            result.is_fallback = False
            return result

        except anthropic.AuthenticationError as e:
            logger.error(f"Anthropic API 인증 실패: {e}. API 키를 확인하세요.")
            return _fallback_classification(articles, reason=f"API 인증 실패: {e}")
        except anthropic.RateLimitError as e:
            logger.warning(f"Rate limit hit: {e}")
            last_error = f"Rate limit: {e}"
            continue
        except json.JSONDecodeError as e:
            logger.error(f"JSON parsing failed: {e}")
            last_error = f"JSON parse error: {e}"
            continue
        except Exception as e:
            logger.error(f"LLM classification failed (attempt {attempt}): {e}", exc_info=True)
            last_error = str(e)
            continue

    return _fallback_classification(articles, reason=last_error or "Unknown error after 3 attempts")


def _fallback_classification(articles: list[ArticleWithContent], reason: str = "") -> ClassifiedOutput:
    """Fallback: put all articles into Deal > 기타. Record failure reason."""
    all_ids = [a.info.id for a in articles]
    logger.warning(f"Using FALLBACK classification for {len(all_ids)} articles. Reason: {reason}")

    return ClassifiedOutput(
        article_order=all_ids,
        is_fallback=True,
        fallback_reason=reason,
        categories=[
            ClassificationCategory(
                name="Deal",
                subcategories=[
                    ClassificationSubcategory(name="경영권 인수 및 매각, 투자 유치"),
                    ClassificationSubcategory(name="투자회수"),
                    ClassificationSubcategory(
                        name="기타",
                        articles=all_ids,
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


def _resort_by_tier(result: ClassifiedOutput, tiers: dict) -> None:
    """Re-sort article lists within each category by importance tier.

    Ascending tier number = more important first.
    Safety net: even if the LLM returned articles in the wrong order,
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
        cat_order: dict[str, int] = {}
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
                if sub.sub_items:
                    # Parent sub has sub_items — skip direct article check
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
    import re as _re

    cls = data.get("classification", {})
    article_order = data.get("article_order", [])

    # Build valid ID set and digit-based reverse mapping for fuzzy matching
    valid_ids = {a.info.id for a in articles}
    id_by_digits: dict[str, str] = {}
    for a in articles:
        digits = _re.sub(r'[^0-9a-fA-F]', '', a.info.id)
        if digits:
            id_by_digits[digits] = a.info.id

    def normalize_ids(raw_ids: list) -> list[str]:
        """Normalize LLM-returned ID list to match actual article IDs."""
        if not isinstance(raw_ids, list):
            return []
        result: list[str] = []
        for rid in raw_ids:
            rid_str = str(rid).strip().strip('"').strip("'")
            # 1) Exact match
            if rid_str in valid_ids:
                result.append(rid_str)
                continue
            # 2) Strip brackets [123] → 123
            cleaned = rid_str.strip('[]').strip()
            if cleaned in valid_ids:
                result.append(cleaned)
                continue
            # 3) Digits/hex only match
            digits = _re.sub(r'[^0-9a-fA-F]', '', rid_str)
            if digits and digits in id_by_digits:
                result.append(id_by_digits[digits])
                continue
            # 4) Substring match
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

    # Add unclassified articles to Deal > 기타
    classified_ids: set[str] = set()
    for cat in categories:
        classified_ids.update(cat.articles)
        for sub in cat.subcategories:
            classified_ids.update(sub.articles)
            for si in sub.sub_items:
                classified_ids.update(si.articles)

    unclassified = all_ids - classified_ids
    if unclassified:
        logger.warning(f"Classification: {len(unclassified)} articles not classified, adding to Deal > 기타")
        if categories and categories[0].subcategories:
            etc_sub = categories[0].subcategories[-1]  # "기타"
            etc_sub.articles.extend(list(unclassified))

    return ClassifiedOutput(categories=categories, article_order=ordered_ids)


def _extract_json(text: str) -> str | None:
    """Extract JSON from LLM response text. Tries multiple strategies."""
    import re

    # Strategy 1: ```json ... ``` blocks
    match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if match:
        candidate = match.group(1).strip()
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            logger.debug("JSON in code block is invalid, trying other strategies")

    # Strategy 2: Find the outermost balanced { ... } using bracket counting
    start = text.find('{')
    if start != -1:
        depth = 0
        in_string = False
        escape_next = False
        for i in range(start, len(text)):
            c = text[i]
            if escape_next:
                escape_next = False
                continue
            if c == '\\' and in_string:
                escape_next = True
                continue
            if c == '"' and not escape_next:
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    candidate = text[start:i + 1]
                    try:
                        json.loads(candidate)
                        return candidate
                    except json.JSONDecodeError:
                        logger.debug(
                            f"Balanced braces found but invalid JSON at position {start}-{i}"
                        )
                        break

    # Strategy 3: Greedy regex fallback
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            json.loads(match.group(0))
            return match.group(0)
        except json.JSONDecodeError:
            logger.debug("Greedy regex JSON extraction failed")

    return None
