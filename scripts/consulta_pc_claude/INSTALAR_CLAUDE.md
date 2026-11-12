# Instalar o Claude Code no PC (uma vez por computador)

Leva uns 5 minutos. Precisa de internet e da conta da empresa no Claude.

## 1. Abrir o PowerShell
Menu Iniciar → digite `PowerShell` → clique em **Windows PowerShell** (não precisa ser administrador).

## 2. Instalar
Cole o comando abaixo e aperte Enter:

```
irm https://claude.ai/install.ps1 | iex
```

Espere terminar. Feche o PowerShell e abra outro (para o Windows reconhecer o comando novo).

*Se o comando acima for bloqueado*, use a alternativa (precisa do Node.js instalado, em nodejs.org, versão LTS):

```
npm install -g @anthropic-ai/claude-code
```

## 3. Conferir
No PowerShell novo:

```
claude --version
```

Se aparecer um número de versão, está instalado. Se aparecer "não reconhecido", feche e abra o PowerShell de novo; se continuar, avise o Zicri.

## 4. Entrar na conta (só na primeira vez)
```
claude
```
O navegador abre para o login. Entre com a **conta da empresa** e volte ao PowerShell. Esse login fica salvo neste PC: nas próximas vezes não pede de novo.

## 5. Usar a consulta
1. Extraia o `terminal-mz.zip` numa pasta (por exemplo, `C:\TerminalMZ`).
2. Dê duplo clique em `iniciar-consulta.bat`.
3. Pergunte em português, por exemplo: *quantos checkouts tenho em aberto?*

Só funciona dentro da rede da empresa. Não pede a senha do MZ App.

## Problemas comuns
| Mensagem | O que fazer |
|---|---|
| `claude` não é reconhecido | Feche e abra o PowerShell. Se persistir, veja "Corrigir o PATH" no fim deste arquivo |
| Não consegui falar com o servidor | Confira se o PC servidor está ligado e se este PC está na rede da empresa |
| Pede login de novo | Refaça o passo 4 |
| Execução de scripts desabilitada | No PowerShell: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` e confirme com `S` |

## Corrigir o PATH (instalou, mas o `claude` não chama)
O instalador grava o programa em `C:\Users\<você>\.local\bin`, e o Windows pode não ter essa pasta no PATH.

1. Confira se o arquivo existe:
```
Test-Path "$env:USERPROFILE\.local\bin\claude.exe"
```
   `True` = está instalado, só falta o PATH. `False` = refaça o passo 2.
2. Adicione a pasta ao PATH do seu usuário:
```
$p = [Environment]::GetEnvironmentVariable("Path","User"); [Environment]::SetEnvironmentVariable("Path", "$p;$env:USERPROFILE\.local\bin", "User")
```
3. Feche o PowerShell, abra outro e rode `claude --version`.

Enquanto isso não for resolvido, o `iniciar-consulta.bat` já acha o Claude nessa pasta e abre normalmente.
