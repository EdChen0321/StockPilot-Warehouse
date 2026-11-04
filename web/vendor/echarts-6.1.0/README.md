# Apache ECharts 6.1.0

Pinned browser distribution, served locally by ops.server; no npm build or framework migration.

- Source: https://cdn.jsdelivr.net/npm/echarts@6.1.0/dist/echarts.min.js
- Release: https://github.com/apache/echarts/releases/tag/6.1.0
- Documentation: https://echarts.apache.org/handbook/en/get-started/
- LICENSE and NOTICE are retained alongside the distribution.
- Runtime does not request any CDN; existing Content-Security-Policy remains unchanged.
- Chart integration: web/sales-charts.js. Use ECharts SVG renderer and ResizeObserver.
