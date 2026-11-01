// CloudFront Function (viewer request) on the API behaviours. It sets X-Harnesswalk-Viewer-Ip
// to the viewer's address as CloudFront saw it (a bare IPv4 or IPv6 address), replacing any
// value the viewer sent. Caddy turns it into the single X-Forwarded-For hop the API trusts
// for the per-IP rate limit (HARNESSWALK_TRUST_PROXY=1).
function handler(event) {
  var request = event.request;
  request.headers['x-harnesswalk-viewer-ip'] = { value: event.viewer.ip };
  return request;
}
