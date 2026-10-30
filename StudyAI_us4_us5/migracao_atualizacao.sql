-- ============================================================
-- Migração: aplicar a uma base de dados "aplicacao" JÁ EXISTENTE.
-- (Numa instalação nova basta executar o ficheiro "bd - cópia.sql".)
-- ============================================================
USE aplicacao;

-- 1. Documentos comprovativos das habilitações (diplomas, certificados).
--    Os ficheiros ficam em disco (uploads/habilitacoes); aqui guarda-se a referência.
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

-- 2. O histórico passa a registar também documentos adicionados/removidos.
--    ATENÇÃO: executar UMA só vez (dá erro "Duplicate column" se já existir).
ALTER TABLE HistoricoHabilitacoes
    ADD COLUMN detalhe VARCHAR(255) NULL
    COMMENT 'Ex.: Documento adicionado/removido (NULL = alteração ao texto)'
    AFTER habilitacoes_novas;

-- 3. Níveis de escolaridade unificados (registo, perfil e pesquisa usam a mesma lista).
--    Os estudantes com "superior" passam a "universitario". Os valores antigos "basico" e "outro"
--    continuam a ser mostrados; o estudante pode escolher um novo nível quando editar o perfil.
UPDATE Estudante SET nivel_ensino = 'universitario' WHERE nivel_ensino = 'superior';

-- 4. Evitar dois administradores com o mesmo username.
--    (Se já existirem repetidos, remove-os primeiro.)
ALTER TABLE Administrador ADD UNIQUE KEY uk_admin_username (username);
