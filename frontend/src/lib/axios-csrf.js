import axios from 'axios'
import { getCsrfFromDocument } from '@src/lib/utils'

// The API enforces Django's CSRF check for session-authenticated requests.
// Send the token only to our own origin, so it never leaks to other hosts.
// The cookie is read on every request because Django rotates the token on login.
const csrfToken = () => {
  const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/)
  return match ? decodeURIComponent(match[1]) : getCsrfFromDocument()
}

const isSameOrigin = (config) => {
  try {
    return new URL(config.url, config.baseURL || window.location.href).origin === window.location.origin
  } catch (e) {
    return false
  }
}

axios.interceptors.request.use((config) => {
  if (isSameOrigin(config)) {
    const token = csrfToken()
    if (token) {
      config.headers['X-CSRFToken'] = token
    }
    config.headers['X-Requested-With'] = 'XMLHttpRequest'
  }
  return config
})

export default axios
