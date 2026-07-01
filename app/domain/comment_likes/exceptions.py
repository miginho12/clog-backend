"""CommentLikes 도메인 예외."""


class CommentLikeDomainError(Exception):
    """CommentLikes 도메인 기본 예외."""

    pass


class CommentLikeTargetNotFound(CommentLikeDomainError):
    """좋아요 대상 댓글이 없음 (또는 삭제됨/접근 불가)."""

    def __init__(self, comment_id: str):
        self.comment_id = comment_id
        super().__init__(f"comment like target not found: {comment_id}")
