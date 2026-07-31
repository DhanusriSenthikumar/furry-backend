class Pagination:
    def __init__(self, page: int = 1, page_size: int = 20):
        self.page = page
        self.page_size = page_size

    @property
    def skip(self) -> int:
        return (self.page - 1) * self.page_size
