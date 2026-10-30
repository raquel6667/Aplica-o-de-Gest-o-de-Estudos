# Aplicacao-de-Gestao-de-Estudos
## Plataforma web que liga estudantes e explicadores, com gestão de disciplinas e avaliações, agendamento de sessões, pagamentos, chat e ferramentas de estudo com apoio de IA.

# StudyAI

Plataforma de explicações (Flask + MySQL). Esta versão inclui as US1 a US7 (utilizadores, disciplinas, explicações,
**pagamentos com PayPal sandbox**, **sincronização com o Google Calendar**, **amigos e chat privado** e
**material de estudo com IA**).

## Arrancar
```bash
pip install -r Requirements.txt
cp .env.example .env        # e preencher (ver abaixo)
# Base de dados nova:        executar "bd - cópia.sql"
# Base de dados já existente: executar, por ordem e uma só vez cada, os ficheiros
#   migracao_atualizacao.sql   (só se ainda não tiver as habilitações/histórico novos)
#   migracao_us4_us5.sql
#   migracao_us6_us7.sql
python app.py
```
`FLASK_DEBUG=1` no `.env` liga o modo de desenvolvimento. Em produção a comunicação tem de usar HTTPS (RNF3).

A sessão de login não expira por tempo: termina ao carregar em **Sair** ou ao fechar o browser.



## PayPal (US4)
- Fica sempre no **sandbox** (o endereço está fixo no código): nunca se movimenta dinheiro real.
- O valor (17,00 €) e a divisão 10 % / 90 % são definidos no servidor (`PRECO_SESSAO`, `PERCENTAGEM_COMISSAO` em `app.py`).
- O estudante aprova no PayPal e regressa a `/pagamentos/paypal/retorno`; o servidor captura a ordem (confirmação servidor-a-servidor).
- **Webhook** (confirmação com assinatura verificada): no developer.paypal.com crie um webhook para
  `https://<PUBLIC_BASE_URL>/paypal/webhook` com o evento `PAYMENT.CAPTURE.COMPLETED` e copie o *Webhook ID* para `PAYPAL_WEBHOOK_ID`.
  O PayPal tem de conseguir chegar ao URL (em desenvolvimento use um túnel HTTPS, ex.: ngrok).
- O **repasse ao explicador é simulado** (fica registado um id `SIMULADO-…`); acontece quando a sessão está concluída e paga.

## Google Calendar (US5)
1. Google Cloud Console → ativar a **Google Calendar API** → ecrã de consentimento → criar credenciais *OAuth client ID* (Aplicação Web).
2. URI de redirecionamento autorizado: `<PUBLIC_BASE_URL>/google/callback` (ex.: `http://localhost:5000/google/callback`).
3. Em modo de teste, adicionar as contas Google de teste à lista de utilizadores de teste.
- Permissão pedida: apenas `calendar.events` (criar e apagar eventos no calendário principal). Não é login.
- Definições em **Calendário Google** (menu lateral): ligar/desligar a conta e a sincronização, tentar novamente eventos falhados.

## Amigos e chat (US6)
- **Amigos** (menu lateral): pesquisa por username (sem distinguir maiúsculas), pedidos enviados/recebidos, aceitar,
  recusar, cancelar e remover. Explicadores por validar, contas eliminadas e o Administrador não aparecem.
- **Conversas**: chat 1 para 1 entre amigos ou, entre Estudante e Explicador, a partir do primeiro pedido de sessão
  (sem amizade, só o Estudante inicia). O tempo real é feito por *polling* HTTPS: a página do chat pergunta ao servidor
  por mensagens novas a cada segundo (≤ 2 s, RNF1) e o menu atualiza os contadores a cada 10 s.
- **Ficheiros no chat** (📎): PDF, DOCX, PPTX, XLSX, TXT, JPG, PNG, MP4 e MP3, até 10 MB. O tipo é confirmado pelo
  conteúdo (um executável com outra extensão é recusado). Ficam em `uploads/chat` com nome aleatório e só abrem pela
  rota protegida, para os dois participantes enquanto a conversa estiver acessível.
- **Reclamações**: na conversa, "Reclamar desta conversa" (motivo obrigatório). O Administrador vê-as em
  **Reclamações**, abre só a conversa reclamada em modo só leitura (com os ficheiros) e marca-as como resolvidas.
  Cada abertura fica registada (quem e quando) e aparece na própria página.
- **Moderação** (Administrador → Reclamações → abrir uma reclamação):
  - *Recusar* a reclamação (motivo obrigatório): fecha-a sem consequências para o utilizador reclamado.
  - *Bloquear* a conta 1, 7 ou 30 dias: não entra (vê o motivo e o prazo), não aparece nas pesquisas, a sessão
    aberta termina logo; as sessões marcadas mantêm-se. No fim do prazo a conta volta sozinha a ativa.
  - *Banir*: permanente (só o Administrador levanta); cancela as sessões futuras, com reembolso das pagas no PayPal.
  - *Denunciar*: regista a denúncia e gera um dossiê (conta, conversa, ficheiros, acessos e histórico) para
    imprimir/guardar em PDF e entregar às autoridades. Nada é enviado automaticamente.
  - Todas as ações ficam no histórico de moderação da conta (quem, quando, motivo).
- O utilizador pode **remover a fotografia de perfil** no seu perfil.
- O username passa a ter 3 a 20 caracteres (letras, números, `.` e `_`) e é único sem distinguir maiúsculas (US1.03).
- Login: 10 tentativas falhadas a partir do mesmo IP travam novas tentativas durante 15 minutos, sem bloquear a conta.
  O contador fica em memória do processo (reinicia quando a aplicação reinicia).

## Material de estudo (US7)
- **Material de estudo** (menu lateral): resumos (até 5 000 caracteres), questionários de escolha múltipla
  (2 a 6 opções, exatamente 1 correta) e flashcards (frente até 300, verso até 1 000). Privado: só o criador o vê.
- Cada material pertence a uma disciplina Ativa do catálogo ou, no caso do Estudante, a uma disciplina pessoal.
  Disciplina inativa: o material fica visível e editável com "Disciplina inativa", mas não se cria material novo.
  Disciplina eliminada: o material fica "Sem disciplina". Ao eliminar a conta, o material é apagado.
- **✨ Assistente Gemini** (em Material de estudo): um chat com o Google Gemini dentro do site. Pode-se perguntar
  qualquer coisa sobre a matéria, colar texto (até 10 000 caracteres por mensagem) e pedir resumos, questionários ou
  flashcards (até 20 itens). Quando o Gemini cria material, aparece um cartão com a pré-visualização e o botão
  "Rever e guardar", que abre o rascunho editável noutro separador (só é guardado em "Guardar").
  É preciso aceitar o aviso de que o texto é enviado ao Google Gemini. A conversa só existe no browser: o servidor
  não a guarda (RNF3). Limite de 50 mensagens por utilizador e por dia; tempo máximo de espera de 30 s.
- **Praticar**: o questionário mostra a pontuação e as respostas certas no fim; os flashcards viram-se e
  percorrem-se. Os resultados não são guardados.
- US2.01: uma disciplina do catálogo com associações a perfis já não pode passar a Inativa.

