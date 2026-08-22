const request = require('./request')

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
