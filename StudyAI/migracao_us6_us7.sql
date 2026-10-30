-- ============================================================
-- Migração US6 (Comunicação) e US7 (Ferramentas de Estudo)
-- Aplicar UMA só vez a uma base de dados "aplicacao" JÁ EXISTENTE
-- (depois de migracao_us4_us5.sql).
-- Numa instalação nova basta executar o ficheiro "bd - cópia.sql".
-- ============================================================
USE aplicacao;

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

-- Moderação de contas (bloquear, banir, levantar, denunciar)
ALTER TABLE Utilizador
    ADD COLUMN bloqueado_ate DATETIME NULL COMMENT 'Bloqueio temporário (moderação): a conta volta a ativa a partir desta data' AFTER motivo_rejeicao,
    ADD COLUMN banida TINYINT(1) NOT NULL DEFAULT 0 COMMENT 'Banimento permanente (só o Administrador o levanta)' AFTER bloqueado_ate,
    ADD COLUMN motivo_bloqueio VARCHAR(500) NULL COMMENT 'Motivo mostrado ao utilizador ao tentar entrar' AFTER banida;

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

