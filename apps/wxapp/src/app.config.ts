export default defineAppConfig({
  pages: [
    'pages/login/index',
    'pages/index/index',
    'pages/age-gate/index',
  ],
  subPackages: [
    {
      root: 'subpackages/feature',
      pages: ['pages/sandbox/index', 'pages/health/index'],
    },
    {
      root: 'subpackages/review',
      pages: ['pages/review-upload/index', 'pages/review-result/index'],
    },
    {
      root: 'subpackages/profile',
      pages: ['pages/wrapped/index', 'pages/profile/index'],
    },
  ],
  window: {
    backgroundTextStyle: 'light',
    navigationBarBackgroundColor: '#0F0B1A',
    navigationBarTitleText: 'CareerCoach AI',
    navigationBarTextStyle: 'white',
  },
})
