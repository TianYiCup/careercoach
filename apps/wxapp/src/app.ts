import { PropsWithChildren } from 'react'
import { useLaunch } from '@tarojs/taro'
import './app.scss'

function App({ children }: PropsWithChildren) {
  // No console.* in production — B-6
  useLaunch(() => {
    // Lifecycle hook retained for future init logic (e.g. analytics)
  })

  return children
}

export default App
