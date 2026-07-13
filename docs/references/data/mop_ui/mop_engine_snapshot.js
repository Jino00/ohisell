// Full capture of all MOP SA-engine dashboard endpoints (async, avoids sync-XHR crash)
(function(){
  var sid = sessionStorage.getItem('sessionId'); if(!sid) return 'NO_SESSION';
  var b = 'https://be.mopapp.net'; var q = '?advertiserId=756';
  var eps = {
    'overview': '/v1/dashboard/overview/756',
    'sa_collection_status': '/v1/dashboard/sa/collection/status'+q,
    'sa_collection_items': '/v1/dashboard/sa/collection/items'+q,
    'sa_collection_performances': '/v1/dashboard/sa/collection/performances'+q,
    'sa_projection_predictions': '/v1/dashboard/sa/projection/predictions'+q,
    'sa_projection_planning': '/v1/dashboard/sa/projection/planning'+q,
    'sa_flight_bids': '/v1/dashboard/sa/flight/bids'+q,
    'sa_flight_rankmaint': '/v1/dashboard/sa/flight/rank-maintenance'+q,
    'sa_abnormal_performances': '/v1/dashboard/sa/abnormal/performances'+q,
    'sa_abnormal_urls': '/v1/dashboard/sa/abnormal/urls'+q,
    'sa_abnormal_utms': '/v1/dashboard/sa/abnormal/utms'+q
  };
  window.__eng = {}; window.__engd = 0; window.__engt = Object.keys(eps).length;
  Object.keys(eps).forEach(function(k){
    fetch(b+eps[k], {headers:{'x-session-id':sid,'accept':'application/json'}})
      .then(function(r){ return r.text().then(function(t){ return {s:r.status,t:t}; }); })
      .then(function(o){ window.__eng[k] = {status:o.status, len:o.t.length, body:o.t}; window.__engd++; })
      .catch(function(e){ window.__eng[k] = {error:String(e)}; window.__engd++; });
  });
  return 'KICKED '+window.__engt;
})();
