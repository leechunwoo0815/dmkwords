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
      try {
        var windowInfo = wx.getWindowInfo();
        this.setData({ isIOS: windowInfo.platform === 'ios' });
      } catch (e) {}
    }
  },
  methods: {
    onTap() {
      if (this.data.disabled) return;
      this.triggerEvent('pay');
    }
  }
});
