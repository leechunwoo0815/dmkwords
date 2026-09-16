Component({
  properties: {
    // 2026-09-16：图标改成自家绘本风资产（/icons/ui/*.png），别再传 emoji——
    // emoji 三端渲染不一致、颜色与令牌无关（错误库 §八十）
    iconName: { type: String, value: 'empty' },
    title: { type: String, value: '暂无数据' },
    desc: { type: String, value: '' },
    btnText: { type: String, value: '' },
  },
  methods: {
    onAction() { this.triggerEvent('action'); }
  }
});
