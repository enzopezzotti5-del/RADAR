const DANGEROUS_CHARS = /[;&|`$(){}\\!\n\r]/
const PATH_TRAVERSAL = /\.\./

export function validateFilePath(path: string, fieldName: string): string | null {
  if (!path) return null
  if (PATH_TRAVERSAL.test(path)) return `${fieldName} não pode conter ".."`
  if (path.startsWith('/')) return `${fieldName} deve ser um caminho relativo`
  return null
}

export function validateCommand(command: string): string | null {
  if (!command) return null
  if (DANGEROUS_CHARS.test(command)) return 'Comando contém caracteres proibidos'
  if (!command.startsWith('python -m ')) return 'Comando deve iniciar com "python -m "'
  return null
}

export function validateRobotForm(data: Record<string, any>): Record<string, string> {
  const errors: Record<string, string> = {}
  if (!data.name?.trim()) errors.name = 'Nome é obrigatório'
  if (!data.type) errors.type = 'Tipo é obrigatório'
  if (!data.repository?.trim()) errors.repository = 'Repositório é obrigatório'
  if (!data.branch?.trim()) errors.branch = 'Branch é obrigatória'
  if (!data.main_file_path?.trim()) errors.main_file_path = 'Caminho do arquivo é obrigatório'
  if (!data.execution_command?.trim()) errors.execution_command = 'Comando é obrigatório'

  const pathErr = validateFilePath(data.main_file_path || '', 'Caminho do arquivo')
  if (pathErr) errors.main_file_path = pathErr
  const depsErr = validateFilePath(data.dependencies_path || '', 'Dependências')
  if (depsErr) errors.dependencies_path = depsErr
  const cmdErr = validateCommand(data.execution_command || '')
  if (cmdErr) errors.execution_command = cmdErr

  return errors
}
