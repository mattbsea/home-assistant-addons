# Changelog

## 1.0.5

- Fix ingress 404: authenticate before disabling requireLogin when password is set

## 1.0.4

- Enable HTTP request logging (console + file)
- Add log_level option (debug/info/warn/error)
- Request logs retained 30 days, up to 200k rows

## 1.0.3

- Fix ingress 404: disable dashboard login requirement after startup
- Add curl for in-container API calls

## 1.0.2

- Fix base image (use hassio-addons/base:21.0.0 instead of archived base-nodejs)
- Install Node.js and npm via apk

## 1.0.1

- Fix Dockerfile base image

## 1.0.0

- Initial release
- OmniRoute AI gateway with 268+ providers
- Ingress dashboard
- Auto-fallback routing
- Token compression (RTK + Caveman)
