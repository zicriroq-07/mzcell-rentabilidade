"""
marketplaces/consulta/catalogo.py — Catálogo das consultas e das palavras que o interpretador reconhece
MZ App

Vai para o banco (consulta.db) ao subir o servidor. Para acrescentar uma consulta: executor em executores.py + uma entrada aqui.
Palavras sem acento e em minúsculas; frases valem mais que palavras soltas. A tabela sem_resposta do banco mostra o que as
pessoas perguntaram e o interpretador não entendeu: é a lista do que falta cadastrar.
params: nome, obrigatorio. Módulos: quem tem algum deles (ou "todos") pode fazer a consulta.
"""

CATALOGO = [
    {"id": "checkout_abertos", "titulo": "Pedidos sem checkout", "modulos": ["expedicao"],
     "descricao": "Quantos pedidos de hoje (e atrasados de dias anteriores) ainda não têm checkout, por canal e pedido a pedido.",
     "palavras": ["checkout", "checkouts", "sem checkout", "em aberto", "pendentes", "pendente", "faltam", "falta bipar", "nao bipado", "sem bipar", "atrasados", "atrasado"],
     "params": [{"nome": "plataforma", "obrigatorio": False}], "exemplo": "quantos checkouts tenho em aberto hoje? (ou: ... da shopee)"},
    {"id": "expedicao_resumo", "titulo": "Resumo do dia da Expedição", "modulos": ["expedicao"],
     "descricao": "Pedidos do dia, checkouts feitos, pendentes, atrasados, cortes e contagem por canal.",
     "palavras": ["resumo", "expedicao", "como esta o dia", "painel", "cortes", "corte", "pedidos do dia", "pedidos de hoje", "quantos pedidos", "andamento", "status do dia"],
     "params": [], "exemplo": "resumo da expedição de hoje"},
    {"id": "full_envios", "titulo": "Envios Full/FBA e coletas", "modulos": ["expedicao"],
     "descricao": "Envios do Full (Meli), Amazon FBA e Shopee em montagem: unidades prontas, coleta e prazo.",
     "palavras": ["full", "envio full", "envios full", "coleta", "coletas", "montagem", "fba", "inbound", "envios"],
     "params": [], "exemplo": "quais envios full estão em montagem e quando é a coleta?"},
    {"id": "cancelamentos", "titulo": "Cancelados no ML ainda ativos no Bling", "modulos": ["expedicao", "sac"],
     "descricao": "Pedidos cancelados no Mercado Livre que ainda não estão cancelados no Bling.",
     "palavras": ["cancelamento", "cancelamentos", "cancelado", "cancelados", "cancelou", "pedido cancelado", "pedidos cancelados"],
     "params": [], "exemplo": "quais pedidos cancelados ainda estão ativos no bling?"},
    {"id": "pedido", "titulo": "Situação de um pedido no Bling", "modulos": ["expedicao", "sac", "meli"],
     "descricao": "Procura o pedido (número da loja ou do Bling) nos Blings 1, 2 e 3: situação, itens, serviço de envio e rastreio.",
     "palavras": ["pedido", "status do pedido", "situacao do pedido", "onde esta o pedido", "rastreio", "bling", "nota", "venda"],
     "params": [{"nome": "numero", "obrigatorio": True}], "exemplo": "situação do pedido 2000012345678901"},
    {"id": "estoque", "titulo": "Saldo de estoque de um SKU", "modulos": ["expedicao", "meli", "amazon", "shopee", "magalu"],
     "descricao": "Saldo físico e virtual do SKU (ou dos que começam com ele) nos Blings 1, 2 e 3. Só leitura.",
     "palavras": ["estoque", "saldo", "quanto tem", "quantos temos", "quantas unidades", "disponivel", "unidades em estoque"],
     "params": [{"nome": "sku", "obrigatorio": True}, {"nome": "conta", "obrigatorio": False}], "exemplo": "estoque do KIT2-MIC-SEMFIO-PRETO"},
    {"id": "sac_resumo", "titulo": "Pendências do SAC", "modulos": ["sac"],
     "descricao": "Perguntas, mensagens e reclamações pendentes por conta do Mercado Livre.",
     "palavras": ["sac", "pendencias", "pendencias do sac", "perguntas pendentes", "reclamacoes abertas", "reclamacoes pendentes", "quantas reclamacoes", "mensagens pendentes"],
     "params": [], "exemplo": "quantas pendências tem no sac?"},
    {"id": "reclamacao", "titulo": "Reclamação de um pedido do ML", "modulos": ["sac"],
     "descricao": "Procura a reclamação do pedido nas contas meli01/02/03: tipo, etapa, status, motivo e as últimas mensagens.",
     "palavras": ["reclamacao", "reclamacoes", "reclamacao do pedido", "claim", "disputa", "devolucao", "mediacao", "reclamou", "motivo da reclamacao"],
     "params": [{"nome": "numero", "obrigatorio": True}, {"nome": "conta", "obrigatorio": False}], "exemplo": "qual a reclamação do pedido 2000012345678901?"},
    {"id": "ajuda", "titulo": "O que posso perguntar", "modulos": ["todos"],
     "descricao": "Lista as consultas que o seu acesso permite.",
     "palavras": ["ajuda", "o que posso perguntar", "comandos", "menu", "o que voce faz"],
     "params": [], "exemplo": "ajuda"},
]
