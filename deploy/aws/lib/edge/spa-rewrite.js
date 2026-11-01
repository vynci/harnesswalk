// CloudFront Function (viewer request) on the S3 behaviour only. A path without a file
// extension is a client-side route of the single-page app, so it is served /index.html.
//
// This replaces distribution-wide custom error responses (403/404 -> /index.html): those apply
// to every behaviour, so an API 404 (a cross-tenant id) would come back as a 200 HTML page.
function handler(event) {
  var request = event.request;
  var uri = request.uri;
  if (uri === '/' || uri.lastIndexOf('.') <= uri.lastIndexOf('/')) {
    request.uri = '/index.html';
  }
  return request;
}
