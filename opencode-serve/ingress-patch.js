(function(){
  var m=location.pathname.match(/^(\/api\/hassio_ingress\/[^/]+)/);
  if(!m)return;
  var P=m[1];
  function rw(u){
    if(typeof u!=='string')return u;
    if(u.indexOf(P)===0)return u;
    if(u.indexOf(location.origin)===0)
      return location.origin+P+u.slice(location.origin.length);
    if(u.charAt(0)==='/')return P+u;
    return u;
  }
  var of=window.fetch;
  window.fetch=function(i,init){
    if(typeof i==='string')i=rw(i);
    else if(i instanceof Request)i=new Request(rw(i.url),i);
    return of.call(this,i,init);
  };
  var O=window.EventSource;
  function E(u,o){return new O(rw(u),o)}
  E.prototype=O.prototype;
  E.CONNECTING=O.CONNECTING;
  E.OPEN=O.OPEN;
  E.CLOSED=O.CLOSED;
  window.EventSource=E;
})();
