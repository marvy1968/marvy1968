"""
Entry Expert
"""

class EntryExpert:

    def evaluate(self,candidate):

        if candidate["grade"] in ("A+","A","A-"):
            return True

        return False
