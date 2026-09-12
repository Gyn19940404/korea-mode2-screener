韩股模式2选股器 V0.8 · 在线数据版

目标
====
不再让你的电脑运行 Python / BAT。
网页部署到 GitHub Pages 后，由 GitHub 云端自动获取 KOSPI + KOSDAQ 全市场数据并发布。

这版只解决：
1. KOSPI + KOSDAQ 全市场
2. 最近约65个真实交易日日K
3. 市值
4. 当天涨幅 / 当天成交额
5. MA5 / MA10 / MA20 / MA30 / MA60
6. 1周涨幅
7. 距5日线
8. 最近20个交易日“每天成交额≥500亿”的判断
9. 连续上涨天数

暂时不解决：
- 强势板块算法
- 板块核心TOP1
- 主升形态识别
- 真实分时

部署一次以后：
- 你的电脑不需要 Python
- 不需要 BAT
- 韩国交易日收盘后，GitHub Actions 会自动更新
- 打开 GitHub Pages 网页即可使用

GitHub 部署
===========
1. 新建一个 GitHub 仓库，例如：korea-mode2
2. 把本压缩包解压后的“所有文件和文件夹”上传到仓库根目录
   必须包含隐藏目录：.github
3. 仓库 Settings → Pages
4. Build and deployment → Source 选择 GitHub Actions
5. 进入 Actions 页签
6. 打开“更新韩股数据并部署网页”
7. 点 Run workflow
8. 成功后，Settings → Pages 会显示网页地址

以后不需要手动运行。
工作日韩国时间16:30左右会自动重新抓数据并部署。

注意
====
- GitHub Actions 的定时任务运行时间可能有少量延迟。
- 当前板块字段故意显示“板块待接入”，不是错误。
- 如果云端行情抓取失败，工作流会失败，之前已经发布成功的网页不会被覆盖。
