"""Comments 도메인 예외."""


class CommentDomainError(Exception):
    """Comments 도메인 기본 예외."""

    pass


class CommentTargetNotFound(CommentDomainError):
    """댓글 대상 게시물이 없음 (또는 비공개라 접근 불가)."""

    def __init__(self, log_id: str):
        self.log_id = log_id
        super().__init__(f"comment target not found: {log_id}")


class CommentNotFound(CommentDomainError):
    """댓글을 찾을 수 없음 (또는 삭제됨)."""

    def __init__(self, comment_id: str):
        self.comment_id = comment_id
        super().__init__(f"comment not found: {comment_id}")


class CommentForbidden(CommentDomainError):
    """본인 소유가 아닌 댓글을 수정/삭제하려 함."""

    def __init__(self, comment_id: str):
        self.comment_id = comment_id
        super().__init__(f"not the owner of comment: {comment_id}")


class CommentParentInvalid(CommentDomainError):
    """대댓글 부모가 유효하지 않음 (다른 게시물이거나 존재 안 함)."""

    def __init__(self, parent_id: str):
        self.parent_id = parent_id
        super().__init__(f"invalid comment parent: {parent_id}")
