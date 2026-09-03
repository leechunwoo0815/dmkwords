const request = require('./request')
// F-L7/T34：TODO——本模块引用的后端端点未实现（consent 三段式=PRD F3），
//   废弃或实现待产品定（域F LOW 清偿登记）；未接入任何页面

function checkText(content) {
  if (!content || typeof content !== 'string' || content.trim().length === 0) {
    return Promise.resolve(true)
  }
  return request.post('/security/check-text', { content: content }, { auth: true })
    .then(function (res) {
      if (res && res.passed) { return true }
      throw new Error(res && res.message ? res.message : '内容包含违规信息，请修改后重试')
    })
}

module.exports = { checkText }
