# Requisitos não funcionais
## RNF1 - Tempo de resposta no máximo de 3 segundos e média de 1 segundo em todas as páginas, com até 100 utilizadores em simultâneo e 100 disciplinas registadas.
## RNF2 - Acesso condicionado por username e password, seguindo o paradigma RBAC com três papéis (Administrador, Estudante e Explicador). A sessão expira após 30 minutos de inatividade e não há bloqueio de conta por tentativas falhadas.
## RNF3 - Proteção de dados pessoais: as passwords são guardadas apenas como hash, a plataforma nunca guarda o número do cartão de crédito (pagamentos tratados por um serviço externo) e toda a comunicação usa HTTPS.
## RNF4 - Comunicação segura entre chats: mensagens encriptadas em trânsito (HTTPS/WebSocket seguro), acessíveis apenas aos dois participantes; o administrador só as consulta quando existe uma reclamação.
## RNF6 - Arquitetura web-based, compatível com Edge, Firefox, Chrome e Safari (duas versões estáveis mais recentes).
## RNF7 - Consumo de APIs externas limitado ao Zoom e a um serviço de pagamentos. Novas APIs só são integradas depois de registadas neste requisito, com finalidade e custo estimado.
## RNF8 - Manutenção de código: código legível e organizado, testes automáticos nas funções críticas, controlo de versões (Git), README e segredos fora do código.
## RNF9 - Design responsivo na interface do utilizador (telemóvel, tablet e computador), a partir de 360 px de largura.
## RNF10 - Calendário pessoal, visível apenas após login: o Estudante e o Explicador veem o seu; o Administrador não tem calendário. Não existe calendário público.



# Regras de escrita de código
## O código em linguagem Python 3.14
## O nome das variáveis deve seguir a notação Snake




# Requisitos funcionais para o 1 Sprint
## US1.01 Como administrador, quero autenticar-me com o username e password. 
## US2.01 Como administrador, quero registar as disciplinas




# Requitos funcionais para o 2 Sprint
## US1 Gestão de Utilizadores e Acesso
## US1.02 Como Utilizador, quero autenticar-me na plataforma através das minhas credenciais, para aceder de forma segura à minha área pessoal.
## US1.03 Como Utilizador, quero registar os dados do meu perfil, para completar a minha informação na plataforma.
## US1.04C omo utilizador autenticado, quero gerir os meus dados pessoais e as disciplinas associadas ao meu perfil, para manter a minha informação atualizada e organizar as disciplinas que frequento ou leciono.

## US2 Gestão Académica
## US2.02 Como estudante, quero registar as avaliações associadas a cada disciplina.
## US2.03 Como estudante, quero que o sistema calcule automaticamente as médias das minhas avaliações por disciplina e a minha média global.

## US3 Gestão de explicações (pesquisa e sessões)
## US3.01 Como estudante, quero pesquisar e filtrar explicadores através de filtros avançados, nomeadamente: disciplina; nível de escolaridade; preço por hora; modalidade (presencial ou online).
## US3.02 Como utilizador, quero agendar sessões de explicação.
## US3.03Como utilizador, quero integrar o Zoom para a realização de sessões online.

## US4 Gestão de Pagamentos
## US4.01 Como estudante ou explicador, quero processar de forma segura os pagamentos das sessões de explicação.
## US4.02 Como estudante ou explicador, quero consultar o histórico das minhas transações de pagamento

## US5 Gestão de Calendário
## US5.01 Como estudante ou explicador, quero  integrar o Google Calendar, para sincronizar automaticamente o meu calendário pessoal com as sessões de explicação agendadas e os eventos da plataforma.

## US6 Gestão de Comunicação
## US6.01 Como estudante ou explicador, quero gerir a minha lista de amigos, incluindo o envio, aceitação, recusa e remoção de pedidos de amizade.
## US6.02 Como estudante ou explicador, quero comunicar com outros utilizadores através de chat privado em tempo real.
## US6.03Como estudante ou explicador, quero partilhar documentos e ficheiros multimédia com outros utilizadores.

## US7 Gestão de Ferramentas de Estudo
## US7.01 Como estudante ou explicador, quero criar resumos, questionários (escolha múltipla) e flashcards, manualmente ou com apoio de Inteligência Artificial.