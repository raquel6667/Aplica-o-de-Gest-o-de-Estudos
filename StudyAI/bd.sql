-- 1. Criar a base de dados
CREATE DATABASE IF NOT EXISTS aplicacao;
USE aplicacao;

-- 2. Criar tabela Administrador
CREATE TABLE IF NOT EXISTS Administrador (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(50) NOT NULL UNIQUE,
    email VARCHAR(255) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    nome VARCHAR(100) NOT NULL,
    nivel_acesso VARCHAR(50) DEFAULT 'admin'
);

-- 2. Utilizador (Estudantes e Explicadores)
CREATE TABLE IF NOT EXISTS Utilizador (
    id_utilizador INT AUTO_INCREMENT PRIMARY KEY,
    tipo ENUM('estudante', 'explicador') NOT NULL,
    username VARCHAR(50) NOT NULL UNIQUE,
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    nome VARCHAR(100) NOT NULL,
    data_nascimento DATE NULL,
    localizacao VARCHAR(100) NULL,
    fotografia_url VARCHAR(255) NULL,
    estado_conta ENUM('pendente', 'ativa', 'rejeitada', 'bloqueada') NOT NULL DEFAULT 'pendente',
    motivo_rejeicao VARCHAR(500) NULL,
    bloqueado_ate DATETIME NULL COMMENT 'Bloqueio temporário (moderação): a conta volta a ativa a partir desta data',
    banida TINYINT(1) NOT NULL DEFAULT 0 COMMENT 'Banimento permanente (só o Administrador o levanta)',
    motivo_bloqueio VARCHAR(500) NULL COMMENT 'Motivo mostrado ao utilizador ao tentar entrar',
    conta_eliminada TINYINT(1) NOT NULL DEFAULT 0,
    nome_original VARCHAR(100) NULL,
    data_eliminacao DATETIME NULL,
    criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- 3. Estudante (dados específicos)
CREATE TABLE IF NOT EXISTS Estudante (
    id_utilizador INT PRIMARY KEY,
    instituicao VARCHAR(150) NULL,
    nivel_ensino VARCHAR(50) NULL,
    CONSTRAINT fk_estudante_utilizador FOREIGN KEY (id_utilizador)
        REFERENCES Utilizador(id_utilizador) ON DELETE CASCADE
);

-- 4. Explicador (dados específicos)
CREATE TABLE IF NOT EXISTS Explicador (
    id_utilizador INT PRIMARY KEY,
    modalidade ENUM('presencial', 'online', 'ambas') NULL,
    localidade VARCHAR(100) NULL,
    apresentacao TEXT NULL,
    biografia TEXT NULL,
    habilitacoes TEXT NULL,
    CONSTRAINT fk_explicador_utilizador FOREIGN KEY (id_utilizador)
        REFERENCES Utilizador(id_utilizador) ON DELETE CASCADE
);

-- 5. Disciplinas do catálogo (geridas pelo Administrador)
CREATE TABLE IF NOT EXISTS Disciplinas (
    ID_Disciplinas INT AUTO_INCREMENT PRIMARY KEY,
    codigo VARCHAR(20) NOT NULL UNIQUE,
    nome VARCHAR(100) NOT NULL,
    descricao VARCHAR(500) NOT NULL,
    estado ENUM('ativa', 'inativa') NOT NULL DEFAULT 'ativa',
    cod_administrador INT NULL,
    criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_disciplinas_administrador FOREIGN KEY (cod_administrador)
        REFERENCES Administrador(id) ON DELETE SET NULL
);

-- 6. Associação de disciplinas do catálogo ao perfil do utilizador (US1.04)
CREATE TABLE IF NOT EXISTS DisciplinaPerfil (
    id INT AUTO_INCREMENT PRIMARY KEY,
    id_utilizador INT NOT NULL,
    codigo_disciplina VARCHAR(20) NOT NULL,
    criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_disc_perfil (id_utilizador, codigo_disciplina),
    CONSTRAINT fk_dp_utilizador FOREIGN KEY (id_utilizador)
        REFERENCES Utilizador(id_utilizador) ON DELETE CASCADE,
    CONSTRAINT fk_dp_disciplina FOREIGN KEY (codigo_disciplina)
        REFERENCES Disciplinas(codigo) ON DELETE CASCADE
);

-- 7. Níveis de escolaridade que o Explicador leciona (US3.01)
CREATE TABLE IF NOT EXISTS NivelExplicador (
    id INT AUTO_INCREMENT PRIMARY KEY,
    id_utilizador INT NOT NULL,
    nivel VARCHAR(50) NOT NULL,
    UNIQUE KEY uk_nivel_exp (id_utilizador, nivel),
    CONSTRAINT fk_ne_utilizador FOREIGN KEY (id_utilizador)
        REFERENCES Utilizador(id_utilizador) ON DELETE CASCADE
);

-- 8. Histórico de alterações às habilitações do Explicador (US1.04)
CREATE TABLE IF NOT EXISTS HistoricoHabilitacoes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    id_utilizador INT NOT NULL,
    habilitacoes_anteriores TEXT NULL,
    habilitacoes_novas TEXT NOT NULL,
    detalhe VARCHAR(255) NULL COMMENT 'Ex.: Documento adicionado/removido (NULL = alteração ao texto)',
    data_alteracao DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_hh_utilizador FOREIGN KEY (id_utilizador)
        REFERENCES Utilizador(id_utilizador) ON DELETE CASCADE
);

-- 8b. Documentos comprovativos das habilitações do Explicador (diplomas, certificados)
--     Os ficheiros ficam em disco (uploads/habilitacoes); aqui guarda-se a referência.
CREATE TABLE IF NOT EXISTS DocumentoHabilitacao (
    id INT AUTO_INCREMENT PRIMARY KEY,
    id_utilizador INT NOT NULL,
    nome_original VARCHAR(255) NOT NULL,
    nome_ficheiro VARCHAR(100) NOT NULL UNIQUE,
    tamanho INT NOT NULL DEFAULT 0,
    criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_dh_utilizador FOREIGN KEY (id_utilizador)
        REFERENCES Utilizador(id_utilizador) ON DELETE CASCADE
);

-- 9. Disciplinas pessoais do Estudante — independentes do catálogo (US2.02)
CREATE TABLE IF NOT EXISTS DisciplinaPessoal (
    id INT AUTO_INCREMENT PRIMARY KEY,
    id_utilizador INT NOT NULL,
    nome VARCHAR(100) NOT NULL,
    criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_dpes_utilizador FOREIGN KEY (id_utilizador)
        REFERENCES Utilizador(id_utilizador) ON DELETE CASCADE
);

-- 10. Avaliações do Estudante (US2.02)
CREATE TABLE IF NOT EXISTS Avaliacao (
    id INT AUTO_INCREMENT PRIMARY KEY,
    id_disciplina_pessoal INT NOT NULL,
    designacao VARCHAR(100) NOT NULL,
    nota DECIMAL(5,2) NOT NULL,
    escala ENUM('0_20', 'percentagem') NOT NULL DEFAULT '0_20',
    data DATE NOT NULL,
    criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_aval_disc FOREIGN KEY (id_disciplina_pessoal)
        REFERENCES DisciplinaPessoal(id) ON DELETE CASCADE
);

-- 11. Horários disponíveis do Explicador (US3.02)
CREATE TABLE IF NOT EXISTS HorarioDisponivel (
    id INT AUTO_INCREMENT PRIMARY KEY,
    id_explicador INT NOT NULL,
    dia_semana TINYINT NOT NULL COMMENT '0=segunda … 6=domingo',
    hora_inicio TIME NOT NULL,
    hora_fim TIME NOT NULL,
    CONSTRAINT fk_hd_explicador FOREIGN KEY (id_explicador)
        REFERENCES Utilizador(id_utilizador) ON DELETE CASCADE
);

-- 12. Sessões de explicação (US3.02)
CREATE TABLE IF NOT EXISTS Sessao (
    id INT AUTO_INCREMENT PRIMARY KEY,
    id_estudante INT NOT NULL,
    id_explicador INT NOT NULL,
    codigo_disciplina VARCHAR(20) NOT NULL,
    modalidade ENUM('presencial', 'online') NOT NULL,
    data_hora DATETIME NOT NULL,
    estado ENUM('pendente','confirmada','recusada','cancelada','concluida','concluida_nao_paga') NOT NULL DEFAULT 'pendente',
    zoom_meeting_id VARCHAR(100) NULL,
    zoom_join_url VARCHAR(500) NULL,
    zoom_start_url VARCHAR(500) NULL,
    criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_sessao_estudante FOREIGN KEY (id_estudante)
        REFERENCES Utilizador(id_utilizador),
    CONSTRAINT fk_sessao_explicador FOREIGN KEY (id_explicador)
        REFERENCES Utilizador(id_utilizador),
    CONSTRAINT fk_sessao_disciplina FOREIGN KEY (codigo_disciplina)
        REFERENCES Disciplinas(codigo)
);

-- 13. Transações de pagamento (US4.01)
CREATE TABLE IF NOT EXISTS Transacao (
    id INT AUTO_INCREMENT PRIMARY KEY,
    id_sessao INT NOT NULL,
    id_estudante INT NOT NULL,
    id_explicador INT NOT NULL,
    valor DECIMAL(10,2) NOT NULL DEFAULT 17.00,
    comissao DECIMAL(10,2) NOT NULL DEFAULT 1.70,
    valor_explicador DECIMAL(10,2) NOT NULL DEFAULT 15.30,
    estado ENUM('pendente','paga','reembolsada','em_divida','cancelada') NOT NULL DEFAULT 'pendente',
    paypal_order_id VARCHAR(100) NULL,
    paypal_capture_id VARCHAR(100) NULL,
    paypal_payout_id VARCHAR(100) NULL,
    criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_sessao_trans (id_sessao),
    UNIQUE KEY uk_transacao_capture (paypal_capture_id),
    KEY idx_transacao_order (paypal_order_id),
    CONSTRAINT fk_trans_sessao FOREIGN KEY (id_sessao) REFERENCES Sessao(id),
    CONSTRAINT fk_trans_estudante FOREIGN KEY (id_estudante) REFERENCES Utilizador(id_utilizador),
    CONSTRAINT fk_trans_explicador FOREIGN KEY (id_explicador) REFERENCES Utilizador(id_utilizador)
);

-- 14. Integração Zoom (US3.03)
CREATE TABLE IF NOT EXISTS IntegracaoZoom (
    id_utilizador INT PRIMARY KEY,
    access_token TEXT NULL,
    refresh_token TEXT NULL,
    token_expira_em DATETIME NULL,
    zoom_user_id VARCHAR(100) NULL,
    ativa TINYINT(1) NOT NULL DEFAULT 1,
    criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_zoom_utilizador FOREIGN KEY (id_utilizador)
        REFERENCES Utilizador(id_utilizador) ON DELETE CASCADE
);

-- 15. Integração Google Calendar (US5.01)
CREATE TABLE IF NOT EXISTS IntegracaoGoogle (
    id_utilizador INT PRIMARY KEY,
    access_token TEXT NULL,
    refresh_token TEXT NULL,
    token_expira_em DATETIME NULL,
    sincronizacao_ativa TINYINT(1) NOT NULL DEFAULT 0,
    aviso VARCHAR(500) NULL COMMENT 'Aviso por mostrar ao utilizador (acesso revogado, evento não sincronizado)',
    criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_google_utilizador FOREIGN KEY (id_utilizador)
        REFERENCES Utilizador(id_utilizador) ON DELETE CASCADE
);

-- 16. Eventos sincronizados com o Google Calendar (US5.01)
CREATE TABLE IF NOT EXISTS EventoCalendario (
    id INT AUTO_INCREMENT PRIMARY KEY,
    id_sessao INT NOT NULL,
    id_utilizador INT NOT NULL,
    google_event_id VARCHAR(255) NULL,
    estado_sync ENUM('sincronizado','nao_sincronizado','removido') NOT NULL DEFAULT 'nao_sincronizado',
    tentativas INT NOT NULL DEFAULT 0,
    criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_evento_sessao_user (id_sessao, id_utilizador),
    CONSTRAINT fk_ec_sessao FOREIGN KEY (id_sessao) REFERENCES Sessao(id) ON DELETE CASCADE,
    CONSTRAINT fk_ec_utilizador FOREIGN KEY (id_utilizador) REFERENCES Utilizador(id_utilizador) ON DELETE CASCADE
);



-- 17. Amizades e pedidos de amizade (US6.01). Uma só linha por par: id_utilizador_a < id_utilizador_b.
--     Recusar, cancelar ou remover apaga a linha (qualquer um pode voltar a pedir).
CREATE TABLE IF NOT EXISTS Amizade (
    id INT AUTO_INCREMENT PRIMARY KEY,
    id_utilizador_a INT NOT NULL,
    id_utilizador_b INT NOT NULL,
    id_remetente INT NOT NULL,
    estado ENUM('pendente', 'aceite') NOT NULL DEFAULT 'pendente',
    criado_em DATETIME NOT NULL,
    aceite_em DATETIME NULL,
    UNIQUE KEY uk_amizade_par (id_utilizador_a, id_utilizador_b),
    KEY idx_amizade_b (id_utilizador_b),
    CONSTRAINT fk_amizade_a FOREIGN KEY (id_utilizador_a) REFERENCES Utilizador(id_utilizador) ON DELETE CASCADE,
    CONSTRAINT fk_amizade_b FOREIGN KEY (id_utilizador_b) REFERENCES Utilizador(id_utilizador) ON DELETE CASCADE,
    CONSTRAINT fk_amizade_remetente FOREIGN KEY (id_remetente) REFERENCES Utilizador(id_utilizador) ON DELETE CASCADE
);

-- 18. Conversas privadas 1 para 1 (US6.02). Uma por par: id_utilizador_a < id_utilizador_b.
--     O acesso é decidido pela aplicação (amizade ou pedido de sessão); o histórico nunca é apagado.
CREATE TABLE IF NOT EXISTS Conversa (
    id INT AUTO_INCREMENT PRIMARY KEY,
    id_utilizador_a INT NOT NULL,
    id_utilizador_b INT NOT NULL,
    criado_em DATETIME NOT NULL,
    UNIQUE KEY uk_conversa_par (id_utilizador_a, id_utilizador_b),
    KEY idx_conversa_b (id_utilizador_b),
    CONSTRAINT fk_conversa_a FOREIGN KEY (id_utilizador_a) REFERENCES Utilizador(id_utilizador),
    CONSTRAINT fk_conversa_b FOREIGN KEY (id_utilizador_b) REFERENCES Utilizador(id_utilizador)
);

-- 19. Mensagens do chat (US6.02)
CREATE TABLE IF NOT EXISTS Mensagem (
    id INT AUTO_INCREMENT PRIMARY KEY,
    id_conversa INT NOT NULL,
    id_autor INT NOT NULL,
    texto TEXT NOT NULL,
    enviada_em DATETIME NOT NULL,
    lida_em DATETIME NULL COMMENT 'Quando o destinatário a viu (NULL = não lida)',
    KEY idx_mensagem_conversa (id_conversa, id),
    CONSTRAINT fk_mensagem_conversa FOREIGN KEY (id_conversa) REFERENCES Conversa(id),
    CONSTRAINT fk_mensagem_autor FOREIGN KEY (id_autor) REFERENCES Utilizador(id_utilizador)
);

-- 20. Ficheiros partilhados no chat (US6.03). Cada ficheiro é enviado como uma mensagem (texto pode ficar vazio).
--     O ficheiro fica em uploads/chat com um nome aleatório; só abre pela rota protegida.
CREATE TABLE IF NOT EXISTS FicheiroMensagem (
    id INT AUTO_INCREMENT PRIMARY KEY,
    id_mensagem INT NOT NULL UNIQUE,
    nome_original VARCHAR(255) NOT NULL,
    nome_ficheiro VARCHAR(100) NOT NULL,
    extensao VARCHAR(10) NOT NULL,
    tamanho INT NOT NULL,
    CONSTRAINT fk_ficheiro_mensagem FOREIGN KEY (id_mensagem) REFERENCES Mensagem(id)
);

-- 21. Reclamações sobre conversas (US6.04)
CREATE TABLE IF NOT EXISTS Reclamacao (
    id INT AUTO_INCREMENT PRIMARY KEY,
    id_conversa INT NOT NULL,
    id_autor INT NOT NULL,
    id_reclamado INT NOT NULL,
    motivo VARCHAR(1000) NOT NULL,
    estado ENUM('aberta', 'resolvida', 'recusada') NOT NULL DEFAULT 'aberta',
    decisao VARCHAR(1000) NULL COMMENT 'Motivo da decisão do Administrador (obrigatório ao recusar)',
    criada_em DATETIME NOT NULL,
    resolvida_em DATETIME NULL,
    id_admin_resolveu INT NULL,
    KEY idx_reclamacao_conversa (id_conversa),
    CONSTRAINT fk_reclamacao_conversa FOREIGN KEY (id_conversa) REFERENCES Conversa(id),
    CONSTRAINT fk_reclamacao_autor FOREIGN KEY (id_autor) REFERENCES Utilizador(id_utilizador),
    CONSTRAINT fk_reclamacao_reclamado FOREIGN KEY (id_reclamado) REFERENCES Utilizador(id_utilizador),
    CONSTRAINT fk_reclamacao_admin FOREIGN KEY (id_admin_resolveu) REFERENCES Administrador(id)
);

-- 22. Registo de cada acesso do Administrador a uma conversa reclamada (US6.04, RNF5)
CREATE TABLE IF NOT EXISTS AcessoConversaAdmin (
    id INT AUTO_INCREMENT PRIMARY KEY,
    id_reclamacao INT NOT NULL,
    id_admin INT NOT NULL,
    acedido_em DATETIME NOT NULL,
    CONSTRAINT fk_acesso_reclamacao FOREIGN KEY (id_reclamacao) REFERENCES Reclamacao(id),
    CONSTRAINT fk_acesso_admin FOREIGN KEY (id_admin) REFERENCES Administrador(id)
);

-- 23. Material de estudo (US7.01): resumos, questionários e flashcards. Privado: só o criador o vê.
--     Pertence a uma disciplina do catálogo OU (só Estudantes) a uma disciplina pessoal. Se a disciplina for
--     eliminada, a coluna fica NULL e o material passa a "Sem disciplina".
CREATE TABLE IF NOT EXISTS Material (
    id INT AUTO_INCREMENT PRIMARY KEY,
    id_utilizador INT NOT NULL,
    tipo ENUM('resumo', 'questionario', 'flashcards') NOT NULL,
    titulo VARCHAR(100) NOT NULL,
    codigo_disciplina VARCHAR(20) NULL,
    id_disciplina_pessoal INT NULL,
    conteudo TEXT NULL COMMENT 'Texto do resumo (até 5000 caracteres)',
    criado_em DATETIME NOT NULL,
    atualizado_em DATETIME NOT NULL,
    KEY idx_material_utilizador (id_utilizador),
    CONSTRAINT fk_material_utilizador FOREIGN KEY (id_utilizador) REFERENCES Utilizador(id_utilizador) ON DELETE CASCADE,
    CONSTRAINT fk_material_disciplina FOREIGN KEY (codigo_disciplina) REFERENCES Disciplinas(codigo)
        ON DELETE SET NULL ON UPDATE CASCADE,
    CONSTRAINT fk_material_disc_pessoal FOREIGN KEY (id_disciplina_pessoal) REFERENCES DisciplinaPessoal(id)
        ON DELETE SET NULL
);

-- 24. Perguntas de escolha múltipla de um questionário (2 a 6 opções, exatamente 1 correta)
CREATE TABLE IF NOT EXISTS PerguntaQuestionario (
    id INT AUTO_INCREMENT PRIMARY KEY,
    id_material INT NOT NULL,
    ordem INT NOT NULL,
    enunciado VARCHAR(500) NOT NULL,
    CONSTRAINT fk_pergunta_material FOREIGN KEY (id_material) REFERENCES Material(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS OpcaoPergunta (
    id INT AUTO_INCREMENT PRIMARY KEY,
    id_pergunta INT NOT NULL,
    ordem INT NOT NULL,
    texto VARCHAR(300) NOT NULL,
    correta TINYINT(1) NOT NULL DEFAULT 0,
    CONSTRAINT fk_opcao_pergunta FOREIGN KEY (id_pergunta) REFERENCES PerguntaQuestionario(id) ON DELETE CASCADE
);

-- 25. Cartões de um conjunto de flashcards (frente até 300, verso até 1000 caracteres)
CREATE TABLE IF NOT EXISTS Flashcard (
    id INT AUTO_INCREMENT PRIMARY KEY,
    id_material INT NOT NULL,
    ordem INT NOT NULL,
    frente VARCHAR(300) NOT NULL,
    verso VARCHAR(1000) NOT NULL,
    CONSTRAINT fk_flashcard_material FOREIGN KEY (id_material) REFERENCES Material(id) ON DELETE CASCADE
);

-- 26. Mensagens enviadas ao Gemini (limite diário por utilizador, RNF7). Não guarda o texto enviado (RNF3).
CREATE TABLE IF NOT EXISTS GeracaoIA (
    id INT AUTO_INCREMENT PRIMARY KEY,
    id_utilizador INT NOT NULL,
    tipo ENUM('resumo', 'questionario', 'flashcards', 'chat') NOT NULL DEFAULT 'chat',
    criada_em DATETIME NOT NULL,
    KEY idx_geracao_utilizador (id_utilizador, criada_em),
    CONSTRAINT fk_geracao_utilizador FOREIGN KEY (id_utilizador) REFERENCES Utilizador(id_utilizador) ON DELETE CASCADE
);

-- 27. Ações de moderação do Administrador sobre contas (a partir de reclamações):
--     bloqueio temporário, banimento, levantamento e denúncia (dossiê para as autoridades).
CREATE TABLE IF NOT EXISTS AcaoModeracao (
    id INT AUTO_INCREMENT PRIMARY KEY,
    id_utilizador INT NOT NULL,
    id_admin INT NOT NULL,
    id_reclamacao INT NULL,
    tipo ENUM('bloqueio', 'banimento', 'levantamento', 'denuncia') NOT NULL,
    motivo VARCHAR(1000) NOT NULL,
    ate DATETIME NULL COMMENT 'Fim do bloqueio temporário',
    criada_em DATETIME NOT NULL,
    KEY idx_moderacao_utilizador (id_utilizador),
    CONSTRAINT fk_moderacao_utilizador FOREIGN KEY (id_utilizador) REFERENCES Utilizador(id_utilizador),
    CONSTRAINT fk_moderacao_admin FOREIGN KEY (id_admin) REFERENCES Administrador(id),
    CONSTRAINT fk_moderacao_reclamacao FOREIGN KEY (id_reclamacao) REFERENCES Reclamacao(id)
);


INSERT INTO Administrador (username, email, password_hash, nome, nivel_acesso)
SELECT 'admin', 'admin@gmail.com', 'scrypt:32768:8:1$v0Ls2CHetyysYJno$b164e2a8c2edb8cb8790f5c6f0a6b60277c1d6e4d00961c2a354fb007ba1152f5b512dcabde67cab74cc0eb423327dd37dc81f54549ad605ad1095d1f0556b4a', 'GrupoMaravilha', 'admin'
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM Administrador WHERE username = 'admin');