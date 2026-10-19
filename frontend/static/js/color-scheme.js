import { isLocalStorageSupported } from './utils.js'
import * as syndicates from './syndicate.js'


const Themes = {
  Light: 'Light',
  Dark: 'Dark',
  System: 'System',
}

const defaultTheme = (isLocalStorageSupported() ? localStorage.getItem('colorTheme') : Themes.Dark) || Themes.Dark

// CSS Vars

const colors = [
  {
    name: 'primary',
    values: {[Themes.Light]: '#01A299', [Themes.Dark]: '#287474'},
  },
  {
    name: 'primary-hover',
    values: {[Themes.Light]: '#019592', [Themes.Dark]: '#1F5E5E'},
  },
  {
    name: 'primary-muted',
    values: {[Themes.Light]: '#287474', [Themes.Dark]: '#018786'},
  },
  {
    name: 'on-primary',
    values: {[Themes.Light]: '#FFFFFF', [Themes.Dark]: '#FAFAFA'},
  },
  {
    name: 'secondary',
    values: {[Themes.Light]: '#28303A', [Themes.Dark]: '#FAFAFA'},
  },
  {
    name: 'secondary-hover',
    values: {[Themes.Light]: '#424A54', [Themes.Dark]: '#D0D0D0'},
  },
  {
    name: 'on-secondary',
    values: {[Themes.Light]: '#FAFAFA', [Themes.Dark]: '#28303A'},
  },
  {
    name: 'success',
    values: {[Themes.Light]: '#5CB85C', [Themes.Dark]: '#5CB85C'},
  },
  {
    name: 'success-hover',
    values: {[Themes.Light]: '#4CAE4C', [Themes.Dark]: '#4CAE4C'},
  },
  {
    name: 'on-success',
    values: {[Themes.Light]: '#ffffff', [Themes.Dark]: '#ffffff'},
  },
  {
    name: 'danger',
    values: {[Themes.Light]: '#F08070', [Themes.Dark]: '#F08070'},
  },
  {
    name: 'danger-hover',
    values: {[Themes.Light]: '#C2413D', [Themes.Dark]: '#C2413D'},
  },
  {
    name: 'on-danger',
    values: {[Themes.Light]: '#ffffff', [Themes.Dark]: '#ffffff'},
  },
  {
    name: 'warning',
    values: {[Themes.Light]: '#F0AD4E', [Themes.Dark]: '#F0AD4E'},
  },
  {
    name: 'warning-hover',
    values: {[Themes.Light]: '#DB9A3F', [Themes.Dark]: '#DB9A3F'},
  },
  {
    name: 'on-warning',
    values: {[Themes.Light]: '#ffffff', [Themes.Dark]: '#ffffff'},
  },
  {
    name: 'on-warning-2',
    values: {[Themes.Light]: '#000000', [Themes.Dark]: '#000000'},
  },
  {
    name: 'background',
    values: {[Themes.Light]: '#FAFAFA', [Themes.Dark]: '#0D0D0D'},
  },
  {
    name: 'surface-primary',
    values: {[Themes.Light]: '#ffffff', [Themes.Dark]: '#141414'},
  },
  {
    name: 'surface-secondary',
    values: {[Themes.Light]: '#F5F5F5', [Themes.Dark]: '#141414'},
  },
  {
    name: 'overlay',
    values: {[Themes.Light]: '#F5F5F5CC', [Themes.Dark]: '#000000B8'},
  },
  {
    name: 'hover',
    values: {[Themes.Light]: '#66666613', [Themes.Dark]: '#FFFFFF13'},
  },
  {
    name: 'hover-accent',
    values: {[Themes.Light]: '#66666626', [Themes.Dark]: '#C9E0FA26'},
  },
  {
    name: 'divider',
    values: {[Themes.Light]: '#ABB6C2', [Themes.Dark]: '#FAFAFA47'},
  },
  {
    name: 'divider-muted',
    values: {[Themes.Light]: '#FAFAFA1F', [Themes.Dark]: '#FAFAFA4766'},
  },
  {
    name: 'text-primary',
    values: {[Themes.Light]: '#28303A', [Themes.Dark]: '#FAFAFA'},
  },
  {
    name: 'text-secondary',
    values: {[Themes.Light]: '#8A94A2', [Themes.Dark]: '#FAFAFA94'},
  },
  {
    name: 'text-help',
    values: {[Themes.Light]: '#4C9BE8', [Themes.Dark]: '#4C9BE8'},
  },

  {
    name: 'input-background',
    values: {[Themes.Light]: '#E2E8ED', [Themes.Dark]: '#3d383a'},
  },
  {
    name: 'input-placeholder',
    values: {[Themes.Light]: '#28303A80', [Themes.Dark]: '#FAFAFA80'},
  },

  {
    name: 'table-accent',
    values: {[Themes.Light]: '#E3E3E3', [Themes.Dark]: '#283848'},
  },

  // Icon colors
  {
    name: 'icon-tunneling-1',
    values: {[Themes.Light]: '#4E5D6C', [Themes.Dark]: '#EAEAEA'},
  },
  {
    name: 'icon-tunneling-2',
    values: {[Themes.Light]: '#1D2935', [Themes.Dark]: '#CDCDCD'},
  },
]

const urls = [
  {
    name: 'loader',
    values: {[Themes.Light]: 'url("/static/img/loader/loader_light-scheme.svg")', [Themes.Dark]: 'url("/static/img/loader/loader_dark-scheme.svg")'},
  },
]

const shadows = [
  {
    name: 'top-nav',
    values: {[Themes.Light]: '0px 2px 10px rgb(0 0 0 / .1)', [Themes.Dark]: '0px 2px 10px rgb(0 0 0 / .3)'},
  },
]


// Get theme value (exclude "System")
function currentThemeValue(theme, syndicate) {
  const singleThemeSyndicates = {
    jusprin: Themes.Light,
    yumi: Themes.Dark,
    kingroon: Themes.Light,
    mintion: Themes.Light,
  }

  if (syndicate && singleThemeSyndicates[syndicate]) {
    return singleThemeSyndicates[syndicate]
  }

  // Get system settings
  if (theme.value === Themes.System) {
    if (window.matchMedia('(prefers-color-scheme)').media !== 'not all' && window.matchMedia('(prefers-color-scheme: light)').matches) {
      return Themes.Light
    } else {
      return Themes.Dark
    }
  }
  // Return saved value since it's not "System"
  return theme.value
}

function mergeColorOverrides(defaultColors, syndicateColors) {
  const merged = new Map(defaultColors.map(color => [color.name, color]));

  syndicateColors.forEach(color => {
    merged.set(color.name, color);
  });

  return Array.from(merged.values());
}


function initTheme(themeValue, syndicate) {
  const finalColors = syndicate && syndicates[syndicate]
  ? mergeColorOverrides(colors, syndicates[syndicate].colors)
  : colors;

  finalColors.forEach(function(color) {
    document.documentElement.style.setProperty(`--color-${color.name}`, color.values[themeValue])

    if (color.name === 'surface-secondary') {
      // Set the <meta name="theme-color"> tag to theme the browser nav bar (Safari)
      let meta = document.querySelector('meta[name="theme-color"]')
      meta.content = color.values[themeValue]
    }
  })

  urls.forEach(function(url) {
    document.documentElement.style.setProperty(`--url-${url.name}`, url.values[themeValue])
  })

  shadows.forEach(function(shadow) {
    document.documentElement.style.setProperty(`--shadow-${shadow.name}`, shadow.values[themeValue])
  })
}


export {
  Themes,
  defaultTheme,

  colors,
  urls,
  shadows,

  currentThemeValue,
  initTheme,
}
