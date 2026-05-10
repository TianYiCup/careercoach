/** @type {import('@commitlint/types').UserConfig} */
module.exports = {
  extends: ['@commitlint/config-conventional'],
  rules: {
    // type 必须在白名单内
    'type-enum': [
      2,
      'always',
      [
        'feat',     // 新功能
        'fix',      // 修 bug
        'refactor', // 重构（不改行为）
        'docs',     // 仅文档
        'style',    // 代码格式（不改逻辑）
        'test',     // 加/改测试
        'chore',    // 工具链 / 配置
        'perf',     // 性能优化
        'ci',       // CI 配置
        'build',    // 构建系统
        'revert',   // 回滚
      ],
    ],

    // scope 建议（不强制）
    'scope-enum': [
      1,
      'always',
      [
        'web', 'wxapp', 'api',                              // 三端
        'agent', 'llm', 'asr', 'tts', 'mod',                // 后端模块
        'mascot', 'card', 'ui',                             // 业务组件
        'ci', 'docs', 'db', 'deps', 'config',               // 杂项
        'sandbox', 'copilot', 'review', 'auth', 'wrapped',  // Epic
      ],
    ],

    'subject-max-length': [2, 'always', 72],
    'subject-case': [0],   // 关掉，中文不需要 case 检查
    'body-leading-blank': [2, 'always'],
    'footer-leading-blank': [2, 'always'],
  },
};
