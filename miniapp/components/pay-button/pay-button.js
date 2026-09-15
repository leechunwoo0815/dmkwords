// iOS 合规：isIOS 统一走 utils/platform（唯一平台判定实现，2026-09-13 去重）
const { isIOS } = require('../../utils/platform');

Component({
  properties: {
    amount: { type: Number, value: 0 },
    buttonText: { type: String, value: '立即支付' },
    disabled: { type: Boolean, value: false },
  },
  data: {
    isIOS: false,
  },
  lifetimes: {
    attached() {
      this.setData({ isIOS: isIOS() });
    }
  },
  methods: {
    onTap() {
      if (this.data.disabled) return;
      this.triggerEvent('pay');
    }
  }
});
