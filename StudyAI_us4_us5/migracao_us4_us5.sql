-- ============================================================
-- Migração US4 (Pagamentos) e US5 (Google Calendar)
-- Aplicar UMA só vez a uma base de dados "aplicacao" JÁ EXISTENTE
-- (que já tenha a migração anterior das habilitações).
-- Numa instalação nova basta executar o ficheiro "bd - cópia.sql".
-- ============================================================
USE aplicacao;

-- US4: uma captura do PayPal nunca pode ser registada em duas transações (idempotência).
-- (Vários NULL são permitidos: só as transações pagas têm capture id.)
ALTER TABLE Transacao ADD UNIQUE KEY uk_transacao_capture (paypal_capture_id);
ALTER TABLE Transacao ADD KEY idx_transacao_order (paypal_order_id);

-- US5: aviso por mostrar ao utilizador (acesso Google revogado, evento não sincronizado).
ALTER TABLE IntegracaoGoogle
    ADD COLUMN aviso VARCHAR(500) NULL
    COMMENT 'Aviso por mostrar ao utilizador (acesso revogado, evento não sincronizado)'
    AFTER sincronizacao_ativa;

-- Sessões futuras já aceites antes desta versão passam a ter a sua transação pendente
-- (só as futuras: as já passadas não geram dívida retroativa).
INSERT INTO Transacao (id_sessao, id_estudante, id_explicador, valor, comissao, valor_explicador, estado, criado_em)
SELECT s.id, s.id_estudante, s.id_explicador, 17.00, 1.70, 15.30, 'pendente', NOW()
FROM Sessao s
WHERE s.estado = 'confirmada' AND s.data_hora > NOW()
  AND NOT EXISTS (SELECT 1 FROM Transacao t WHERE t.id_sessao = s.id);
