// The production React mirror never talks to PocketBase. This placeholder keeps
// legacy editor-only branches from loading the PocketBase client into the bundle.
const unavailable = () => {
  throw new Error('PocketBase nao esta disponivel neste frontend somente leitura.')
}

const pb = {
  authStore: { isValid: false, record: null, clear: () => undefined, onChange: () => () => undefined },
  collection: unavailable,
  send: unavailable,
  autoCancellation: () => pb,
}

export default pb
