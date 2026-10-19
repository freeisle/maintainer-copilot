package dev.maintainer;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class PomToolsTest {
    @Test void parsesProject() {
        var result = new PomTools().inspectPom("<project><groupId>a</groupId><artifactId>b</artifactId><properties><java.version>17</java.version></properties></project>");
        assertEquals("a",result.get("groupId"));
        assertEquals("17",result.get("javaVersion"));
        assertEquals(false,result.get("resolved"));
    }
    @Test void rejectsExternalEntities() {
        assertThrows(IllegalArgumentException.class, () -> new PomTools().inspectPom("<!DOCTYPE project [<!ENTITY x SYSTEM 'file:///secret'>]><project>&x;</project>"));
    }
    @Test void rejectsMalformedOrOversizedInput() {
        assertThrows(IllegalArgumentException.class, () -> new PomTools().inspectPom("<project>"));
        assertThrows(IllegalArgumentException.class, () -> new PomTools().inspectPom("a".repeat(200001)));
    }
}
