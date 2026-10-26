# 合规口径端上取证（2026-10-26）

## 环境

- 微信开发者工具：`ok:true / loginExpired:false`（Bokoblin）；项目 `/Users/litianyu/cc-projects/dmkwords/miniapp`（原生小程序，AppID `wxcdb…`）。
- 后端 `http://127.0.0.1:8002`（`bash scripts/dev.sh restart` 后），管理端 `:5173` 均 200；演示基线 PASS。
- 模拟器登录：`wx.request` 调 `/api/miniapp/login`（演示家长 `13800008888`，dev code `1234`）写入 token；演示孩 `id=1`。
- 截图尺寸 586×1268（JPEG，开发者工具模拟器）。

## 截图与结论

| 文件 | 页面 | 实测文案（要点） |
|---|---|---|
| `01_service_agreement.jpg` | 服务协议 | 「线下亲子阅读服务」；「随书配套的音频、阅读小挑战、生词本、打卡与成长报告 属于会员服务的线上辅助权益，随借阅服务附赠，不单独收费」；「相关费用为线下会员服务费（观察期/年度）与线下活动服务费；小程序内展示的支付入口仅为付款方式之一」；「押金为图书借阅押金…按实际可退金额退还」 |
| `02_books.jpg` | 阅读空间 | 导航标题「阅读空间」；底部 tab「阅读空间」；书目列表正常 |
| `03_shelf.jpg` | 书架 | 底部 tab「阅读空间」；在借/收藏/预约/已通过四页签正常 |
| `04_purchase.jpg` | 会员购买/续费 | 方案名「线下亲子活动服务费（首场）」「线下会员服务费（观察期）」「线下会员服务费（年度）」；卖点「到店借阅 + 随书配套音频与阅读小挑战」；「线上支付仅为付款方式，活动在馆内线下举行」；置灰原因「如需续期请购买年度会员服务（观察期会员服务仅未入会/已过期/已退会可购）」；「线上支付需微信一键登录…也可到店办理」 |
| `05_book_detail.jpg` | 书籍详情 | 「已读完 100%」「挑战成功！」「最佳表现 100 分」「点卡片看挑战结果」；底部操作「看挑战结果」；预约说明「到店由馆员核销取书」 |
| `06_quiz.jpg` | 阅读小挑战 | 导航「阅读小挑战」；「这本书你已经通过小挑战啦，词数已入账」 |
| `07_index.jpg` | 首页 | 底部 tab「阅读空间」；继续听/推荐正常 |

## 取证过程中发现并修复

购买页「观察期」方案的**置灰原因**来自后端 `backend/domain/identity/order_pricing.py`，原文仍是
「正式会员年费（观察期费仅未入会/已过期/已退会可购）」——前端 wxml/js 静态扫描与 `TYPE_LABELS` 断言都覆盖不到。
已改为「年度会员服务（观察期会员服务仅未入会/已过期/已退会可购）」，并新增判据 ⑤ 扫描**家长可见的后端字符串**
（`tests/unit/test_compliance_copy_20261026.py::test_backend_parent_facing_strings_have_no_forbidden_terms`）。
`04_purchase.jpg` 为修复后的复取。

## 复现命令

```bash
wechatide -c zcode check_wechatide_status
wechatide -c zcode simulator_open_page --project /Users/litianyu/cc-projects/dmkwords/miniapp \
  --page pages/agreement/service-agreement/service-agreement
wechatide -c zcode simulator_screenshot --project /Users/litianyu/cc-projects/dmkwords/miniapp \
  --path docs/compliance-ide/01_service_agreement.jpg --wait 2

# 登录（在模拟器运行时执行；token 写入本地缓存后其余页面可直接打开）
wechatide -c zcode automation_evaluate --project /Users/litianyu/cc-projects/dmkwords/miniapp \
  --fn-source "function(){return new Promise(function(resolve){wx.request({url:'http://127.0.0.1:8002/api/miniapp/login',method:'POST',header:{'content-type':'application/json'},data:{phone:'13800008888',code:'1234'},success:function(r){if(r.data&&r.data.token){wx.setStorageSync('token',r.data.token)}resolve({status:r.statusCode})}})})}"
```

> 说明：本机 `simulator_open_page --query` 对页面 `options` 的传参不稳定（quiz/book-detail 已改用应用内
> `wx.navigateTo` 复核）。截图只证明**渲染层文案**；提审前仍须按 `docs/22 §七` 用真机走购买页/订单列表/
> 微信账单三处核对。
