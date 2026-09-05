import '@testing-library/jest-dom'

// Radix UI's Slider uses ResizeObserver internally; jsdom doesn't have it.
global.ResizeObserver = class ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
