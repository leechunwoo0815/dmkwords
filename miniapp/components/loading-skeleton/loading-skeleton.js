Component({
  properties: {
    loading: { type: Boolean, value: true },
    rows: { type: Number, value: 3 },
    // rows = 头像+行（列表页）；grid = 卡片封面网格（书目类 2 列页）
    type: { type: String, value: 'rows' },
  },
})
