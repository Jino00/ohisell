// MOP 라이브 관찰 스냅샷 스크립트 (A안 — 00.아이폰_17)
// 사용법: MOP 로그인 상태에서 gstack 헤디드 브라우저로
//   B="$HOME/.claude/skills/gstack/browse/dist/browse"
//   "$B" eval <이 파일 경로>   > 결과.json
// 전제: 로그인된 MOP 콘솔 어느 페이지든(sessionStorage.sessionId 존재).
// ★advertiserId=756 (오하이_구민정, 네이버 SA). 1422는 "Google Ads"라 네이버 캠페인 조회 시 전부 "-" 반환(2026-07-12 D+1 실측 정정).
//   광고주 목록: GET /v1/advertisers → 756=오하이_구민정(OPERATE), 1422=Google Ads, 1428=Google Ads_CID, 780=오하이 카카오.
// 매일 같은 시각 실행해 시계열로 쌓으면 MOP 행동 궤적 역설계 재료가 된다.
(function(){
  const sid = sessionStorage.getItem('sessionId');           // x-session-id 인증 (없으면 SESSION_EXPIRE)
  const ADV = '756';                                          // 오하이_구민정(네이버 SA). ← 1422(Google Ads) 아님
  const CID = 'cmp-a001-02-000000009793536';                 // 00.아이폰_17 (쇼핑검색)
  const filterBody = (ids)=>JSON.stringify({
    sa:{naverAdgroupIds:[],kakaoAdgroupIds:[],googleAdgroupIds:[]},
    saShopping:{naverAdgroupIds:[]},
    da:{kakaoAdgroupIds:[],googleAdgroupIds:[],metaAdgroupIds:[],criteoAdgroupIds:[]},
    va:{googleAdgroupIds:[],metaAdgroupIds:[]},
    app:{googleAppgroupIds:[]},
    saCampaigns:{naverCampaignIds:[],kakaoCampaignIds:[],googleCampaignIds:[]},
    saShoppingCampaigns:{naverCampaignIds:ids},               // ← 쇼핑캠페인 필터
    daCampaigns:{kakaoCampaignIds:[],googleCampaignIds:[],metaCampaignIds:[],criteoCampaignIds:[]},
    vaCampaigns:{googleCampaignIds:[],metaCampaignIds:[]},
    appCampaigns:{googleCampaignIds:[]}
  });
  function req(m,url,body){
    try{
      var x=new XMLHttpRequest(); x.open(m,url,false); x.withCredentials=true;
      x.setRequestHeader('x-session-id',sid);
      x.setRequestHeader('Accept','application/json, text/plain, */*');
      if(body!=null) x.setRequestHeader('Content-Type','application/json');
      x.send(body!=null?body:null);
      return {status:x.status, body:x.responseText};
    }catch(e){return {err:String(e)};}
  }
  // 관찰 기간: 어제까지 최근 7일 + 단일 어제. (KST 날짜는 호출 시점에 맞게 조정)
  const base='https://be.mopapp.net/v1/report/campaign/'+ADV;
  return JSON.stringify({
    sid_present: !!sid,
    availablePeriod: req('GET','https://be.mopapp.net/v1/report/available-period/'+ADV+'?reportType=REPORT_CAMPAIGN'),
    adgroupsTree: req('GET', base+'/adgroups'),
    // 아래 기간은 매일 갱신 (예시: 07-05~07-11, 07-11 단일)
    summary_week:  req('POST', base+'/summary?startDate=20260705&endDate=20260711', filterBody([CID])),
    summary_day:   req('POST', base+'/summary?startDate=20260711&endDate=20260711', filterBody([CID])),
    summary_all_week: req('POST', base+'/summary?startDate=20260705&endDate=20260711', filterBody([]))
  });
})()
// TODO(D+1, 데이터 있을 때): 리포트-캠페인에서 "날짜별/매체별" 탭 클릭 시 나가는 시계열 테이블 API를
//   응답캡처 후킹으로 잡아 이 스크립트에 추가 → 일별 노출/클릭/비용/CPC/순위 궤적 확보.
//   (오늘은 지출 0이라 summary가 "-" 반환, 시계열 엔드포인트 미확정)
