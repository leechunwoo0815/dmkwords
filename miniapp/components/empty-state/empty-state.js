Component({
  properties: {
    icon: { type: String, value: '📭' },
    title: { type: String, value: '暂无数据' },
    desc: { type: String, value: '' },
    btnText: { type: String, value: '' },
  },
  methods: {
    onAction() { this.triggerEvent('action'); }
  }
});
